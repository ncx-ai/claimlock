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
from . import importer, merge, ops, refs, selftest
from . import project as P

HINT = {
    "stale": "re-check it (claimlock diff {id}), then: claimlock verify {id}",
    "missing": ("a source does not exist or cannot be read — fix its sources (or the file's "
                "permissions), re-check, then: claimlock verify {id}"),
    "unpinned": "never pinned — re-check it, then: claimlock verify {id}",
    "unanchored": ("the pinned content was never committed or staged — commit the source so every "
                   "clone can see it (if it changed since, re-check, then: claimlock verify {id})"),
}
MARK = {"verified": "✓", "unverified": "?", "refuted": "✗", "owed": "⇢"}

CI_SNIPPET = """\
Add the gate to CI or a pre-commit hook:

    claimlock self-test && claimlock check && claimlock refs

Then: claimlock new <id> --area <area>   (write the claim, cite evidence and sources)
      claimlock verify <id>              (pins every source; only after checking it)
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


def cmd_check(args):
    _, hasher, results = _evaluate(args)
    counts = {"invalid": sum(1 for r in results if r.problems)}
    counts.update({s: sum(1 for r in results if r.state == s) for s in C.NON_FRESH})
    if args.json:
        print(json.dumps({
            "claims": len(results),
            "sources_hashed": hasher.hashed,
            "counts": counts,
            "results": [{
                "id": r.claim.id, "area": r.claim.area, "status": r.claim.status,
                "problems": r.problems, "state": r.state,
                "sources": [{"path": p, "state": s} for p, s in r.per_source],
            } for r in results],
        }, indent=2))
    else:
        for r in results:
            if r.problems:
                print(f"{_paint('31', 'INVALID ')} {r.claim.id}")
                for x in r.problems:
                    print(f"         {x}")
            if r.state in C.NON_FRESH:
                print(f"{_paint('33', r.state.upper().ljust(8))} {r.claim.id}")
                for path, st in r.per_source:
                    if st != "fresh":
                        print(f"         {path}: {st}")
                print(f"         {HINT[r.state].format(id=r.claim.id)}")
        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        print(f"claimlock: {len(results)} claims, {hasher.hashed} sources hashed — {summary}")
    return 1 if any(counts.values()) else 0


def cmd_stale(args):
    _, _, results = _evaluate(args)
    rc = 0
    for r in results:
        if r.state in C.NON_FRESH:
            rc = 1
            paths = ",".join(p for p, s in r.per_source if s != "fresh")
            print(f"{r.claim.id}\t{r.claim.area}\t{r.state}\t{paths}")
    return rc


def cmd_list(args):
    _, _, results = _evaluate(args)
    for r in results:
        if args.status and r.claim.status != args.status:
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
    if c.sources:
        print("Sources (a change here makes this claim stale):")
        for s in c.sources:
            pin = s.blob[:12] if s.blob else "unpinned"
            print(f"  {s.path} — {states.get(s.path, '-')} ({pin})")
    print(f"\nfile: {_rel(project, c.path)}")
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
        for path, blob in pinned:
            print(f"  {path} @ {blob[:12]}")
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
    state, per = C.freshness(c, project, hasher, C.anchors_for(project, c.sources))
    hasher.save()
    if state is None:
        print(f"claimlock: {c.id} is {c.status}; only verified claims have pins")
        return 0
    if state == "fresh":
        print(f"claimlock: {c.id} is fresh — every source matches its pin")
        return 0
    pins = {s.path: s.blob for s in c.sources}
    for path, st in per:
        if st == "fresh":
            continue
        if st == "missing":
            print(f"--- {path}: does not exist or cannot be read")
            continue
        if st == "unpinned":
            print(f"--- {path}: never pinned; nothing to compare against")
            continue
        if st == "unanchored":
            print(f"--- {path}: unchanged since verification, but that content was never committed "
                  f"or staged — commit it so other clones can diff this claim")
            continue
        old = gitio.cat_blob(project.root, pins[path])
        if old is None:
            print(f"--- {path}: changed, but the pinned content {pins[path][:12]} is not in git "
                  f"(never committed, or no repository) — prior content unavailable; "
                  f"re-read the claim against the current file")
            continue
        try:
            new = (project.root / path).read_bytes()
        except OSError as e:
            # A warm stat cache can report `stale` without reading the file.
            print(f"--- {path}: cannot be read ({e.strerror or e})")
            continue
        sys.stdout.writelines(difflib.unified_diff(
            old.decode("utf-8", "replace").splitlines(keepends=True),
            new.decode("utf-8", "replace").splitlines(keepends=True),
            fromfile=f"{path} @ {pins[path][:12]} (verified)", tofile=f"{path} (now)"))
    return 0


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

    add("init", cmd_init, "create .claimlock.toml, claims/ and a .gitignore entry")
    p = add("new", cmd_new, "scaffold an unverified claim")
    p.add_argument("id")
    p.add_argument("--area", default="unfiled")
    p = add("check", cmd_check, "the gate: fail on invalid or non-fresh claims")
    p.add_argument("--json", action="store_true")
    p.add_argument("--area")
    p = add("stale", cmd_stale, "list non-fresh verified claims")
    p.add_argument("--area")
    p = add("list", cmd_list, "list claims")
    p.add_argument("--area")
    p.add_argument("--status", choices=C.STATUSES)
    p = add("search", cmd_search, "case-insensitive substring search")
    p.add_argument("query")
    p = add("show", cmd_show, "one claim with evidence and per-source state")
    p.add_argument("id")
    p = add("verify", cmd_verify, "pin sources and mark verified (only after re-checking)")
    p.add_argument("ids", nargs="+")
    p = add("owe", cmd_owe, "hand off a claim's re-check to someone (status: owed)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--to", help="email of who owes it (default: git config user.email)")
    p.add_argument("--reason", help="one line appended to the claim body")
    p = add("resolve", cmd_resolve, "settle conflicted pins after a merge")
    p.add_argument("ids", nargs="*")
    p = add("diff", cmd_diff, "show what changed in a claim's sources since it was verified")
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
