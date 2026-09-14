"""Command-line front end. Formatting lives here; logic lives in the modules."""
import argparse
import difflib
import json
import os
import sys
from pathlib import Path

from . import VERSION
from . import claims as C
from . import ops
from . import project as P
from . import snapshots

HINT = {
    "stale": "re-check it (claimlock diff {id}), then: claimlock verify {id}",
    "missing": "a source was deleted or renamed — fix its sources, re-check, then: claimlock verify {id}",
    "unpinned": "never pinned — re-check it, then: claimlock verify {id}",
}
MARK = {"verified": "✓", "unverified": "?", "refuted": "✗"}

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


def _project(args):
    return P.load(Path(args.dir) if args.dir else Path.cwd())


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
    print(f"{c.id} ({c.area}) — {c.status}, verified_at {c.verified_at or '-'}")
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


def cmd_diff(args):
    project = _project(args)
    c = _find(project, args.id)
    if c is None:
        print(f"claimlock: no claim {args.id!r}", file=sys.stderr)
        return 1
    hasher = C.open_hasher(project)
    state, per = C.freshness(c, project, hasher)
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
            print(f"--- {path}: deleted or renamed since verification")
            continue
        if st == "unpinned":
            print(f"--- {path}: never pinned; nothing to compare against")
            continue
        old = snapshots.load(project, pins[path])
        if old is None:
            print(f"--- {path}: changed, but the pinned content {pins[path][:12]} is unavailable "
                  f"(not in git, no snapshot) — re-read the claim against the current file")
            continue
        new = (project.root / path).read_bytes()
        sys.stdout.writelines(difflib.unified_diff(
            old.decode("utf-8", "replace").splitlines(keepends=True),
            new.decode("utf-8", "replace").splitlines(keepends=True),
            fromfile=f"{path} @ {pins[path][:12]} (verified)", tofile=f"{path} (now)"))
    return 0


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
    p = add("diff", cmd_diff, "show what changed in a claim's sources since it was verified")
    p.add_argument("id")
    return ap, sub, add


def main(argv) -> int:
    ap, _, _ = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (P.ConfigError, C.StoreMissing) as e:
        print(f"claimlock: {e}", file=sys.stderr)
        return 2
    except ops.Refused as e:
        print(f"claimlock: {e}", file=sys.stderr)
        return 1
