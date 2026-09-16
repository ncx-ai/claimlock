"""Command-line front end. Formatting lives here; logic lives in the modules."""
import argparse
import difflib
import json
import os
import sys
from pathlib import Path

from . import VERSION
from . import claims as C
from . import gitio
from . import hooks
from . import importer, merge, ops, refs, regions, selftest
from . import project as P

HINT = {
    "stale": "re-check it (claimlock diff {id}), then: claimlock verify {id}",
    "missing": ("a source does not exist or cannot be read — fix its sources (or the file's "
                "permissions), re-check, then: claimlock verify {id}"),
    "unpinned": "never pinned — re-check it, then: claimlock verify {id}",
    "unanchored": ("the pinned content was never committed or staged — commit the source so every "
                   "clone can see it (if it changed since, re-check, then: claimlock verify {id})"),
    "renamed": "a source was renamed — run: claimlock follow {id}",
}
MARK = {"verified": "✓", "unverified": "?", "refuted": "✗", "owed": "⇢"}

# Output budgets (docs/specs/2026-09-15-claimlock-output-budget-design.md §3).
# `--full` (or `--body` for search) always restores the uncapped output.
LISTED_CLAIMS = 20   # failing/pre-existing/owed claims listed by `check`
SOURCE_LINES = 3     # per-source detail lines, and per-problem lines, per claim in `check`

CI_SNIPPET = """\
Gate a change in CI on the claims it touched (pre-existing drift is listed, not blocking):

    claimlock self-test && claimlock check --changed origin/main && claimlock refs

Check the whole store locally:

    claimlock check

Then: claimlock new <id> --area <area>   (write the claim, cite evidence and sources)
      claimlock verify <id>              (pins every source; only after checking it)
      claimlock owe <id> --to <email>    (hand the re-check to someone, instead)
"""


def _paint(code, s):
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{s}\033[0m"
    return s


def _base(args):
    """Where a relative path on the command line is resolved from."""
    return Path(args.dir) if args.dir else Path.cwd()


def _project(args):
    return P.load(_base(args))


def _evaluate(args):
    project = _project(args)
    hasher = C.open_hasher(project)
    results = C.evaluate(project, hasher)
    hasher.save()
    if getattr(args, "area", None):
        results = [r for r in results if r.claim.area == args.area]
    return project, hasher, results


def _rel(project, path):
    try:
        return path.relative_to(project.root).as_posix()
    except ValueError:
        return str(path)


def _find(project, cid):
    return next((c for c in C.load_claims(project) if c.id == cid), None)


def cmd_init(args):
    root = Path(args.dir) if args.dir else Path.cwd()
    if not root.is_dir():
        kind = "does not exist" if not root.exists() else "is not a directory"
        print(f"claimlock: {root} {kind}", file=sys.stderr)
        return 2
    for item in ops.init_store(root):
        print(f"created {item}")
    print()
    print(CI_SNIPPET, end="")
    return 0


def cmd_new(args):
    project = _project(args)
    print(_rel(project, ops.new_claim(project, args.id, args.area)))
    return 0


def _failing(r):
    return bool(r.problems) or r.state in C.NON_FRESH


def _capped(items, cap, more):
    """(kept, note | None): items[:cap], and `more.format(n=<left over>)` when
    cut. `cap=None` means unlimited (never cuts)."""
    items = list(items)
    if cap is None or len(items) <= cap:
        return items, None
    return items[:cap], more.format(n=len(items) - cap)


def _hints_block(states_in_order, first_id):
    """The `hints:` lines: one per state present (that has a hint — `invalid`
    does not), `  <state>: <HINT[state] with {id} replaced by first_id[state]>`.
    [] when no such state is present."""
    return [f"  {state}: {HINT[state].format(id=first_id[state])}"
            for state in states_in_order if state in HINT and state in first_id]


def _print_failing(r, cap):
    """`cap`: max problem/source detail lines per claim (SOURCE_LINES, or None
    for --full). The per-state hint is printed once for the whole run by
    `cmd_check`, never here."""
    if r.problems:
        print(f"{_paint('31', 'INVALID ')} {r.claim.id}")
        shown, note = _capped(r.problems, cap, "… and {n} more problems")
        for x in shown:
            print(f"         {x}")
        if note:
            print(f"         {note}")
    if r.state in C.NON_FRESH:
        print(f"{_paint('33', r.state.upper().ljust(8))} {r.claim.id}")
        by_key = {s.key: s for s in r.claim.sources}
        lines = []
        for key, st in r.per_source:
            if st == "fresh":
                continue
            if st == "renamed":
                s = by_key.get(key)
                info = r.renames.get(s.path) if s else None
                if info:
                    new, sha = info
                    lines.append(f"{key}: renamed → {new} ({sha})")
                    continue
            lines.append(f"{key}: {st}")
        shown, note = _capped(lines, cap, "… and {n} more sources")
        for line in shown:
            print(f"         {line}")
        if note:
            print(f"         {note}")


def _owed_line(project, r, state=None, behind_cache=None):
    since = r.claim.owed_since or "?"
    if since in ("?", "none"):
        behind = None
    elif behind_cache is not None and since in behind_cache:
        behind = behind_cache[since]
    else:
        behind = gitio.commits_behind(project.root, since)
        if behind_cache is not None:
            behind_cache[since] = behind  # one `rev-list --count` per distinct owed_since
    ago = "" if behind is None else f", {behind} commit{'' if behind == 1 else 's'} ago"
    note = f" ({state})" if state in C.NON_FRESH else ""
    return f"OWED     {r.claim.id} → {r.claim.owed_by} since {since}{ago}{note}"


def _owed_states(project, hasher, owed):
    """{id: worst source state} for owed claims, their pins evaluated as if
    verified (the same evaluation `diff` and `show` use). Listing only: an
    owed claim never blocks and carries no verdict in `evaluate`."""
    if not owed:
        return {}
    sources = [s for r in owed for s in r.claim.sources if P.safe_source(project.root, s.path) is not None]
    hasher.prime([s.path for s in sources])
    anchors = C.anchors_for(project, sources)
    renames = C.renames_for(project, sources)
    out = {r.claim.id: C.freshness(r.claim, project, hasher, anchors, as_status="verified",
                                    renames=renames)[0] for r in owed}
    hasher.save()
    return out


def _source_entries(r):
    """[{"path": key, "state": state, ...}] for `cmd_check --json`; a
    renamed source gains "renamed_to" (spec §3.2)."""
    by_key = {s.key: s for s in r.claim.sources}
    out = []
    for key, state in r.per_source:
        entry = {"path": key, "state": state}
        if state == "renamed":
            s = by_key.get(key)
            info = r.renames.get(s.path) if s else None
            if info:
                entry["renamed_to"] = info[0]
        out.append(entry)
    return out


def cmd_check(args):
    project, hasher, results = _evaluate(args)
    scope = None
    if args.changed:
        if not gitio.in_git(project.root):
            print("claimlock: --changed needs a git repository", file=sys.stderr)
            return 2
        base = gitio.merge_base(project.root, args.changed)
        if base is None:
            print(f"claimlock: cannot find a merge base between {args.changed!r} and HEAD", file=sys.stderr)
            return 2
        listed = gitio.changed_since(project.root, base)
        if listed is None:
            print(f"claimlock: could not list changes since {base} (git failed)", file=sys.stderr)
            return 2
        changed = set(listed)
        scope = {r.claim.id for r in results
                 if _rel(project, r.claim.path) in changed or any(s.path in changed for s in r.claim.sources)}

    def in_scope(r):
        return scope is None or r.claim.id in scope

    blocking = [r for r in results if _failing(r) and in_scope(r)]
    elsewhere = [r for r in results if _failing(r) and not in_scope(r)]
    owed = [r for r in results if r.claim.status == "owed" and not r.problems]
    blocking_ids = {r.claim.id for r in blocking}
    counts = {"invalid": sum(1 for r in blocking if r.problems)}
    counts.update({s: sum(1 for r in blocking if r.state == s) for s in C.NON_FRESH})
    full = args.full
    cap = None if full else LISTED_CLAIMS
    if args.json:
        kept_ids = {r.claim.id for r in blocking} | {r.claim.id for r in elsewhere} | {r.claim.id for r in owed}
        listed = results if full else [r for r in results if r.claim.id in kept_ids]
        print(json.dumps({
            "claims": len(results),
            "sources_hashed": hasher.hashed,
            "counts": counts,
            "scope": sorted(scope) if scope is not None else None,
            "omitted": len(results) - len(listed),
            "results": [{
                "id": r.claim.id, "area": r.claim.area, "status": r.claim.status,
                "problems": r.problems, "state": r.state,
                "sources": _source_entries(r),
                "in_scope": in_scope(r), "blocking": r.claim.id in blocking_ids,
                "owed_by": r.claim.owed_by,
            } for r in listed],
        }, indent=2))
    else:
        owed_states = _owed_states(project, hasher, owed)
        src_cap = None if full else SOURCE_LINES
        shown, note = _capped(blocking, cap, "… and {n} more failing claims — claimlock check --full")
        for r in shown:
            _print_failing(r, src_cap)
        if note:
            print(note)
        if elsewhere:
            print("pre-existing (not changed here):")
            shown, note = _capped(elsewhere, cap, "  … and {n} more pre-existing claims — claimlock check --full")
            for r in shown:
                print(f"  {r.claim.id}: {'invalid' if r.problems else r.state}")
            if note:
                print(note)
        behind = {}
        shown, note = _capped(owed, cap, "… and {n} more owed claims — claimlock check --full")
        for r in shown:
            print(_owed_line(project, r, owed_states.get(r.claim.id), behind))
        if note:
            print(note)
        first_id = {}
        for r in blocking:
            if r.problems:
                first_id.setdefault("invalid", r.claim.id)
            if r.state in C.NON_FRESH:
                first_id.setdefault(r.state, r.claim.id)
        hints = _hints_block(("invalid", *C.NON_FRESH), first_id)
        if hints:
            print("hints:")
            for h in hints:
                print(h)
        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        if owed:
            summary += f", {len(owed)} owed"
        head = f"{len(results)} claims" + (f" ({len(scope)} in scope)" if scope is not None else "")
        print(f"claimlock: {head}, {hasher.hashed} sources hashed — {summary}")
    return 1 if blocking else 0


def _owner(args, project):
    """The normalized email `--mine` / `--owed-by` filter on, or None for no filter."""
    if getattr(args, "mine", False):
        email = gitio.user_email(project.root)
        if not email:
            raise ops.NeedsIdentity("--mine needs git config user.email (or use --owed-by <email>)")
        return C.normalize_email(email) or email.strip().casefold()
    by = getattr(args, "owed_by", None)
    return None if by is None else (C.normalize_email(by) or by.strip().casefold())


def _owed_to(claim, owner):
    """True for an owed claim whose `owed_by` is `owner` (already normalized)."""
    return claim.status == "owed" and C.normalize_email(claim.owed_by) == owner


def cmd_stale(args):
    project, _, results = _evaluate(args)
    owner = _owner(args, project)
    rc = 0
    for r in results:
        if r.claim.status == "owed" and (owner is None or _owed_to(r.claim, owner)):
            print(f"{r.claim.id}\t{r.claim.area}\towed\t{r.claim.owed_by}")
        elif owner is None and r.state in C.NON_FRESH:
            rc = 1
            keys = ",".join(key for key, state in r.per_source if state != "fresh")
            print(f"{r.claim.id}\t{r.claim.area}\t{r.state}\t{keys}")
    return rc


def cmd_list(args):
    project, _, results = _evaluate(args)
    owner = _owner(args, project)
    for r in results:
        if args.status and r.claim.status != args.status:
            continue
        if owner is not None and not _owed_to(r.claim, owner):
            continue
        flag = f" [{r.state}]" if r.state in C.NON_FRESH else ""
        flag += " [invalid]" if r.problems else ""
        flag += f" [owed → {r.claim.owed_by}]" if r.claim.status == "owed" else ""
        print(f"{MARK.get(r.claim.status, '?')} {r.claim.id} ({r.claim.area}){flag}")
        print(f"    {r.claim.headline()}")
    return 0


def cmd_search(args):
    _, _, results = _evaluate(args)
    q = args.query.lower()
    hits = 0
    for r in results:
        c = r.claim
        refs = " ".join(str(e.get("ref", "")) for e in c.evidence if isinstance(e, dict))
        hay = "\n".join([c.id, c.area, c.body, " ".join(s.path for s in c.sources), refs]).lower()
        if q not in hay:
            continue
        hits += 1
        flag = f" [{r.state}]" if r.state in C.NON_FRESH else ""
        print(f"{c.id} ({c.area}, {c.status}){flag}")
        for line in c.body.splitlines():
            if q in line.lower():
                print(f"    {line.strip()}")
        print()
    if not hits:
        print(f"claimlock: nothing matches {args.query!r}")
        return 1
    return 0


def cmd_show(args):
    project, hasher, results = _evaluate(args)
    r = next((r for r in results if r.claim.id == args.id), None)
    if r is None:
        print(f"claimlock: no claim {args.id!r}", file=sys.stderr)
        return 1
    c = r.claim
    line = f"{c.id} ({c.area}) — {c.status}"
    if c.status == "owed":
        line += f", owed by {c.owed_by or '?'} since {c.owed_since or '?'}"
    print(line)
    if r.state in C.NON_FRESH:
        print(_paint("33", f"{r.state.upper()}: {HINT[r.state].format(id=c.id)}"))
    for x in r.problems:
        print(_paint("31", f"INVALID: {x}"))
    print()
    print(c.body)
    print()
    if c.evidence:
        print("Evidence:")
        for e in c.evidence:
            if isinstance(e, dict):
                print(f"  [{e.get('kind')}] {e.get('ref')}")
    states = dict(r.per_source)
    if _pins_status(c) and not r.problems:
        _, per = C.freshness(c, project, hasher, C.anchors_for(project, c.sources),
                             as_status=_pins_status(c), renames=C.renames_for(project, c.sources))
        hasher.save()
        states = dict(per)
    if c.sources:
        print("Sources (a change here makes this claim stale):")
        in_git = gitio.in_git(project.root)
        for s in c.sources:
            pin = s.pin[:12] if s.pin else "unpinned"
            v = _verified(project, in_git, _rel(project, c.path), s)
            note = {"unpinned": "", "unknown": " — verified by unknown (no git)",
                    "uncommitted": " — uncommitted (verifier known once committed)"}.get(v[0])
            if note is None:
                note = f" — verified by {v[1]} at {v[2]} ({v[3]})"
            state = states.get(s.key, "-")
            reason = ""
            if state == "missing" and s.region is not None:
                _, why = hasher.region(s.path, s.region)
                reason = f": {why}" if why else ""
            print(f"  {s.key} — {state} ({pin}){reason}{note}")
    print(f"\nfile: {_rel(project, c.path)}")
    return 0


def _verified(project, in_git, claim_rel, source):
    """("unpinned",) | ("unknown",) | ("uncommitted",) | ("verified", email, iso, sha)."""
    if not source.pin:
        return ("unpinned",)
    if not in_git:
        return ("unknown",)
    field, value = ("hash", source.hash) if source.region is not None else ("blob", source.blob)
    v = gitio.verifier(project.root, claim_rel, field, value)
    return ("verified", *v) if v else ("uncommitted",)


def cmd_who(args):
    project = _project(args)
    c = _find(project, args.id)
    if c is None:
        print(f"claimlock: no claim {args.id!r}", file=sys.stderr)
        return 1
    in_git = gitio.in_git(project.root)
    for s in c.sources:
        v = _verified(project, in_git, _rel(project, c.path), s)
        print("\t".join([s.key, *(v[1:] if v[0] == "verified" else v)]))
    return 0


def cmd_verify(args):
    project = _project(args)
    rc = 0
    for cid in args.ids:
        try:
            pinned = ops.verify(project, cid)
        except ops.Refused as e:
            print(f"claimlock: {e}", file=sys.stderr)
            rc = 1
            continue
        print(f"verified {cid}")
        for key, pin in pinned:
            print(f"  {key} @ {pin[:12]}")
    return rc


def cmd_follow(args):
    project = _project(args)
    rc = 0
    for cid in args.ids:
        try:
            moves = ops.follow(project, cid)
        except ops.Refused as e:
            print(f"claimlock: {e}", file=sys.stderr)
            rc = 1
            continue
        for old, new, state in moves:
            print(f"followed {cid}: {old} → {new} ({state})")
    return rc


def cmd_owe(args):
    project = _project(args)
    rc = 0
    for cid in args.ids:
        try:
            by, since = ops.owe(project, cid, to=args.to, reason=args.reason)
        except ops.Refused as e:
            print(f"claimlock: {e}", file=sys.stderr)
            rc = 1
            continue
        print(f"owed {cid} → {by} (since {since})")
    return rc


def cmd_resolve(args):
    project = _project(args)
    claims = C.load_claims(project)
    by_id = {c.id: c for c in claims}
    rc = 0
    for cid in args.ids:
        if cid not in by_id:
            print(f"claimlock: no claim {cid!r}", file=sys.stderr)
            rc = 1
    targets = [by_id[i] for i in args.ids if i in by_id] if args.ids else [c for c in claims if c.conflicted]
    if not targets and not args.ids:
        print("claimlock: no conflicted claims")
    for c in targets:
        if not c.conflicted:
            print(f"-      {c.id}  not conflicted")
            continue
        outcome, message = merge.resolve_claim(project, c)
        print(f"{outcome.upper():6} {c.id}  {message}")
        if outcome == "left":
            rc = 1
    return rc


def cmd_diff(args):
    project = _project(args)
    c = _find(project, args.id)
    if c is None:
        print(f"claimlock: no claim {args.id!r}", file=sys.stderr)
        return 1
    hasher = C.open_hasher(project)
    renames = C.renames_for(project, c.sources)
    state, per = C.freshness(c, project, hasher, C.anchors_for(project, c.sources),
                             as_status=_pins_status(c), renames=renames)
    hasher.save()
    if state is None:
        print(f"claimlock: {c.id} is {c.status}; only verified or owed claims have pins")
        return 0
    if state == "fresh":
        print(f"claimlock: {c.id} is fresh — every source matches its pin")
        return 0
    srcs = {s.key: s for s in c.sources}
    for key, st in per:
        if st == "fresh":
            continue
        s = srcs.get(key)
        if st == "renamed":
            new, sha = renames.get(s.path, (None, None)) if s else (None, None)
            print(f"--- {key}: renamed to {new} in {sha} — run: claimlock follow {c.id}")
            continue
        if st == "missing":
            if s is not None and s.region is not None:
                _, reason = hasher.region(s.path, s.region)
                print(f"--- {key}: {reason or 'does not exist or cannot be read'}")
            else:
                print(f"--- {key}: does not exist or cannot be read")
            continue
        if st == "unpinned":
            print(f"--- {key}: never pinned; nothing to compare against")
            continue
        if st == "unanchored":
            print(f"--- {key}: unchanged since verification, but that content was never committed "
                  f"or staged — commit it so other clones can diff this claim")
            continue
        if not s.blob:
            print(f"--- {key}: changed, but no blob pinned — prior content unavailable; "
                  f"re-read the claim against the current file (run: claimlock check)")
            continue
        old = gitio.cat_blob(project.root, s.blob)
        if old is None:
            print(f"--- {key}: changed, but the pinned content {s.blob[:12]} is not in git "
                  f"(never committed, or no repository) — prior content unavailable; "
                  f"re-read the claim against the current file")
            continue
        if s.region is not None:
            try:
                old_region = regions.extract(old, s.region)
            except regions.RegionError as e:
                print(f"--- {key}: the region cannot be found in the pinned content ({e})")
                continue
            try:
                new = (project.root / s.path).read_bytes()
            except OSError as e:
                print(f"--- {key}: cannot be read ({e.strerror or e})")
                continue
            try:
                new_region = regions.extract(new, s.region)
            except regions.RegionError as e:
                # per_source already read "missing" for this key when the
                # current file's region cannot be extracted, so this branch is
                # not expected to run — kept as a loud fallback rather than
                # a silent no-op if that ever stops being true.
                print(f"--- {key}: {e}")
                continue
            old_lines, new_lines = _text_lines(old_region.encode("utf-8")), _text_lines(new_region.encode("utf-8"))
            if old_lines == new_lines:
                continue
            print("\n".join(difflib.unified_diff(
                old_lines, new_lines, fromfile=f"{key} @ {s.hash[:12]} (verified)",
                tofile=f"{key} (now)", lineterm="")))
            continue
        try:
            new = (project.root / s.path).read_bytes()
        except OSError as e:
            # A warm stat cache can report `stale` without reading the file.
            print(f"--- {key}: cannot be read ({e.strerror or e})")
            continue
        # Compared as lines without their endings: git serves the pinned blob
        # normalized (LF) while a core.autocrlf=true checkout holds CRLF, and
        # comparing with the endings kept marks every line changed.
        old_lines, new_lines = _text_lines(old), _text_lines(new)
        if old_lines == new_lines:
            if old != new:
                print(f"--- {key}: only line endings differ from the pinned content")
            continue
        print("\n".join(difflib.unified_diff(
            old_lines, new_lines, fromfile=f"{key} @ {s.blob[:12]} (verified)",
            tofile=f"{key} (now)", lineterm="")))
    return 0


def _text_lines(data):
    """Lines broken at "\\n" only, each without a trailing "\\r". `str.splitlines`
    also breaks at form feeds, vertical tabs and Unicode separators, which
    git does not treat as line ends — a diff would then show a changed line
    in pieces."""
    lines = data.decode("utf-8", "replace").split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _pins_status(claim):
    """The status to evaluate a claim's pins as, for `diff` and `show`: an owed
    claim's pins are what was last verified, so they read as if verified."""
    return "verified" if claim.status == "owed" else None


def cmd_refs(args):
    project = _project(args)
    ids = {c.id for c in C.load_claims(project)}
    markers, scanned = refs.scan(project)
    dangling = [m for m in markers if m.id not in ids]
    for m in dangling:
        print(f"DANGLING {m.path}:{m.line}  Claim `{m.id}` names no claim")
    print(f"claimlock: {len(markers)} markers in {scanned} files scanned, {len(dangling)} dangling")
    return 1 if dangling else 0


def cmd_affected(args):
    project = _project(args)
    base = _base(args)
    wanted = set()
    for a in args.paths:
        p = Path(a)
        p = (p if p.is_absolute() else base / p).resolve()
        try:
            wanted.add(p.relative_to(project.root).as_posix())
        except ValueError:
            print(f"claimlock: {a} is outside the project root; ignored", file=sys.stderr)
    hasher = C.open_hasher(project)
    for r in C.evaluate(project, hasher):
        for s in r.claim.sources:
            if s.path in wanted:
                print(f"{r.claim.id}\t{r.state or r.claim.status}\t{s.path}")
    hasher.save()
    return 0


def cmd_import(args):
    project = _project(args)
    raw = Path(args.src)
    src = (raw if raw.is_absolute() else _base(args) / raw).resolve()
    if not src.is_dir():
        kind = "does not exist" if not src.exists() else "is not a directory"
        print(f"claimlock: {src} {kind}", file=sys.stderr)
        return 2
    ids, errors = importer.import_dir(project, src)
    for e in errors:
        print(f"claimlock: {e}", file=sys.stderr)
    noun = "claim" if len(ids) == 1 else "claims"
    print(f"imported {len(ids)} {noun}; each is UNPINNED until re-checked and verified")
    return 1 if errors else 0


def cmd_self_test(args):
    return selftest.run()


def cmd_hook(args):
    return hooks.main(args.event, sys.stdin.read(), dict(os.environ))


def build_parser():
    ap = argparse.ArgumentParser(prog="claimlock",
                                 description="Claims pinned to the content that could falsify them.")
    ap.add_argument("--version", action="version", version=f"claimlock {VERSION}")
    ap.add_argument("-C", "--dir", help="run as if started in this directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        return p

    add("init", cmd_init, "create .claimlock.toml, claims/, and .gitignore and .gitattributes entries")
    p = add("new", cmd_new, "scaffold an unverified claim")
    p.add_argument("id")
    p.add_argument("--area", default="unfiled")
    p = add("check", cmd_check, "the gate: fail on invalid or non-fresh claims")
    p.add_argument("--json", action="store_true")
    p.add_argument("--full", action="store_true",
                   help="list every claim and source line, uncapped (text and --json both)")
    p.add_argument("--area")
    p.add_argument("--changed", metavar="BASE",
                   help="block only on claims whose sources or files changed since the merge base with BASE "
                        "(committed changes only — uncommitted edits are not in scope; run plain "
                        "'claimlock check' for the whole working tree)")
    p = add("stale", cmd_stale, "list non-fresh verified claims")
    p.add_argument("--area")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--owed-by", metavar="EMAIL", help="only claims owed by this email")
    g.add_argument("--mine", action="store_true", help="only claims owed by your git config user.email")
    p = add("list", cmd_list, "list claims")
    p.add_argument("--area")
    p.add_argument("--status", choices=C.STATUSES)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--owed-by", metavar="EMAIL", help="only claims owed by this email")
    g.add_argument("--mine", action="store_true", help="only claims owed by your git config user.email")
    p = add("search", cmd_search, "case-insensitive substring search")
    p.add_argument("query")
    p = add("show", cmd_show, "one claim with evidence and per-source state")
    p.add_argument("id")
    p = add("verify", cmd_verify, "pin sources and mark verified (only after re-checking)")
    p.add_argument("ids", nargs="+")
    p = add("follow", cmd_follow, "rewrite a renamed source's path to where it moved, keeping its pins")
    p.add_argument("ids", nargs="+")
    p = add("owe", cmd_owe, "hand off a claim's re-check to someone (status: owed)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--to", help="email of who owes it (default: git config user.email)")
    p.add_argument("--reason", help="one line appended to the claim body")
    p = add("resolve", cmd_resolve, "settle conflicted pins after a merge")
    p.add_argument("ids", nargs="*")
    p = add("diff", cmd_diff, "show what changed in a claim's sources since it was verified")
    p.add_argument("id")
    p = add("who", cmd_who, "who verified each of a claim's pins, from git history")
    p.add_argument("id")
    add("refs", cmd_refs, "fail on Claim markers that name no claim")
    p = add("affected", cmd_affected, "claims whose sources include these paths")
    p.add_argument("paths", nargs="+")
    p = add("import", cmd_import, "import claims from the original ground-truth format")
    p.add_argument("src")
    add("self-test", cmd_self_test, "prove the detectors can fail")
    p = add("hook", cmd_hook, "Claude Code hook entry point (always exits 0)")
    p.add_argument("event")
    return ap, sub, add


def main(argv) -> int:
    # A source path that names a non-UTF-8 filesystem entry decodes (via
    # os.fsdecode/surrogateescape) to a string holding a lone surrogate.
    # `print`-ing it under stdout/stderr's default strict encoding raises and
    # crashes the whole command; surrogateescape here round-trips it back to
    # the file's exact original bytes instead, the same way a terminal shows
    # `ls`'s output for such a name.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="surrogateescape")
        except (AttributeError, ValueError):
            pass
    ap, _, _ = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (P.ConfigError, C.StoreMissing) as e:
        print(f"claimlock: {e}", file=sys.stderr)
        return 2
    except ops.NeedsIdentity as e:
        print(f"claimlock: {e}", file=sys.stderr)
        return 2
    except ops.Refused as e:
        print(f"claimlock: {e}", file=sys.stderr)
        return 1
