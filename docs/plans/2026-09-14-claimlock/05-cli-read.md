### Task 5: CLI front end — `init`, `new`, `check`, `stale`, `list`, `search`, `show`

**Files:**
- Create: `lib/claimlock/ops.py` (only `Refused`, `init_store`, `new_claim` in this task), `lib/claimlock/cli.py`, `tests/test_cli_read.py`
- Modify: `tests/helpers.py` (hoist `pinned_text` from `tests/test_claims.py`), `tests/test_claims.py` (import it from helpers; delete the local copy)

**Interfaces:**
- Consumes: `project.load/ConfigError/CONFIG`, `claims.*` (Tasks 3–4), `frontmatter.quote` (Task 2).
- Produces: `ops.Refused`, `ops.init_store(root) -> list[str]`, `ops.new_claim(project, cid, area) -> Path`; `cli.main(argv) -> int`, `cli.build_parser()`, and the `-C/--dir` global option. Later tasks add subcommands by extending `build_parser()` with functions named `cmd_<name>`.

- [ ] **Step 1: Hoist the fixture**

Move `pinned_text` verbatim from `tests/test_claims.py` into `tests/helpers.py`; in `tests/test_claims.py` change the import line to
`from helpers import TmpCase, claim_text, make_repo, pinned_text, write`. Run `python3 -m unittest discover -s tests` → `Ran 33 tests ... OK`.

- [ ] **Step 2: Write the failing tests**

`tests/test_cli_read.py`:
```python
import json
import unittest

from helpers import TmpCase, claim_text, make_repo, pinned_text, run_cli, write
from claimlock.pins import blob_of_bytes


class Init(TmpCase):
    def test_init_creates_store_once(self):
        d = self.tmp / "p"
        d.mkdir()
        write(d, ".gitignore", "node_modules/")
        rc, out, err = run_cli(d, "init")
        self.assertEqual(rc, 0, err)
        self.assertTrue((d / ".claimlock.toml").is_file())
        self.assertTrue((d / "claims").is_dir())
        self.assertEqual((d / ".gitignore").read_text(), "node_modules/\n.claimlock/\n")
        self.assertIn("claimlock check", out)
        rc, _, err = run_cli(d, "init")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)
        self.assertEqual((d / ".gitignore").read_text().count(".claimlock/"), 1)


class New(TmpCase):
    def test_new_scaffolds_a_valid_unverified_claim(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, out, err = run_cli(root, "new", "my-claim", "--area", "api")
        self.assertEqual(rc, 0, err)
        self.assertIn("claims/my-claim.md", out)
        self.assertEqual(run_cli(root, "check")[0], 0)
        self.assertEqual(run_cli(root, "new", "my-claim")[0], 1)
        rc, _, err = run_cli(root, "new", "Bad_Id")
        self.assertEqual(rc, 1)
        self.assertIn("kebab-case", err)


class Check(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "one\n")
        write(self.root, "b.py", "bee\n")
        write(self.root, "claims/fresh-one.md", pinned_text("fresh-one", [("a.py", blob_of_bytes(b"one\n"))]))
        write(self.root, "claims/stale-one.md", pinned_text("stale-one", [("b.py", blob_of_bytes(b"old\n"))]))
        write(self.root, "claims/unpinned-one.md", pinned_text("unpinned-one", [("a.py", None)]))
        write(self.root, "claims/invalid-one.md", claim_text("invalid-one", status="maybe"))

    def test_human_output_and_census(self):
        rc, out, err = run_cli(self.root, "check")
        self.assertEqual(rc, 1, err)
        self.assertIn("STALE    stale-one", out)
        self.assertIn("b.py: stale", out)
        self.assertIn("UNPINNED unpinned-one", out)
        self.assertIn("INVALID  invalid-one", out)
        self.assertIn("status 'maybe'", out)
        self.assertNotIn("fresh-one", out)
        self.assertIn("claimlock: 4 claims, 3 sources hashed — 1 invalid, 1 unpinned, 1 stale, 0 missing", out)

    def test_json(self):
        rc, out, _ = run_cli(self.root, "check", "--json")
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual((data["claims"], data["sources_hashed"]), (4, 3))
        self.assertEqual(data["counts"], {"invalid": 1, "unpinned": 1, "stale": 1, "missing": 0})
        by_id = {r["id"]: r for r in data["results"]}
        self.assertEqual(by_id["stale-one"]["sources"], [{"path": "b.py", "state": "stale"}])

    def test_area_filter_and_clean_exit(self):
        for cid in ("stale-one", "unpinned-one", "invalid-one"):
            (self.root / "claims" / f"{cid}.md").unlink()
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 0)
        self.assertIn("1 claims, 1 sources hashed — 0 invalid, 0 unpinned, 0 stale, 0 missing", out)
        rc, out, _ = run_cli(self.root, "check", "--area", "elsewhere")
        self.assertEqual(rc, 0)
        self.assertIn("0 claims", out)

    def test_stale_list_search_show(self):
        rc, out, _ = run_cli(self.root, "stale")
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(l.split("\t")[0] for l in out.splitlines()), ["stale-one", "unpinned-one"])

        rc, out, _ = run_cli(self.root, "list", "--status", "verified")
        self.assertEqual(rc, 0)
        self.assertIn("fresh-one", out)
        self.assertIn("stale-one (core) [stale]", out)
        self.assertNotIn("invalid-one", out)

        self.assertEqual(run_cli(self.root, "search", "HOLDS")[0], 0)
        rc, out, _ = run_cli(self.root, "search", "zebra")
        self.assertEqual(rc, 1)
        self.assertIn("nothing matches", out)

        rc, out, err = run_cli(self.root, "show", "stale-one")
        self.assertEqual(rc, 0, err)
        self.assertIn("stale-one (core) — verified", out)
        self.assertIn("[test] s::c", out)
        self.assertIn("b.py — stale", out)
        self.assertEqual(run_cli(self.root, "show", "nope")[0], 1)


class Unreadable(TmpCase):
    def test_missing_claims_dir_is_exit_2(self):
        root = make_repo(self.tmp / "r", use_git=False)
        (root / "claims").rmdir()
        rc, _, err = run_cli(root, "check")
        self.assertEqual(rc, 2)
        self.assertIn("no claims directory", err)

    def test_bad_config_is_exit_2(self):
        root = make_repo(self.tmp / "r", use_git=False, config="bogus = 1\n")
        rc, _, err = run_cli(root, "check")
        self.assertEqual(rc, 2)
        self.assertIn("unknown key", err)

    def test_empty_store_reports_zero(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 0)
        self.assertIn("0 claims, 0 sources hashed", out)

    def test_dir_option(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, out, _ = run_cli(self.tmp, "-C", str(root), "check")
        self.assertEqual(rc, 0)
        self.assertIn("0 claims", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: `test_cli_read` tests FAIL/ERROR with `ModuleNotFoundError: No module named 'claimlock.cli'` in stderr (rc 1 from the launcher's import).

- [ ] **Step 4: Implement `lib/claimlock/ops.py`**

```python
"""Operations that change files. Each refuses rather than guessing."""
from pathlib import Path

from . import claims as C
from .frontmatter import quote
from .project import CONFIG


class Refused(Exception):
    pass


CONFIG_TEMPLATE = """\
# claimlock configuration. Every key is optional; the defaults are shown.
# claims_dir = "claims"
# marker_globs = ["**/*.md"]
# marker_pattern = 'Claim: `([a-z0-9][a-z0-9-]*)`'
"""

CLAIM_TEMPLATE = """\
---
id: {id}
area: {area}
status: unverified
evidence: []
sources: []
---
<One sentence stating the claim, in the present tense.>

<Why it is true: the enforcement site, and what would falsify it. If it is a
number, how it was measured.>
"""


def init_store(root) -> list:
    root = Path(root).resolve()
    cfg = root / CONFIG
    if cfg.exists():
        raise Refused(f"{cfg} already exists")
    created = []
    cfg.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    created.append(CONFIG)
    claims_dir = root / "claims"
    if not claims_dir.is_dir():
        claims_dir.mkdir()
        created.append("claims/")
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".claimlock/" not in existing.splitlines():
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        gi.write_text(existing + sep + ".claimlock/\n", encoding="utf-8")
        created.append(".gitignore (+ .claimlock/)")
    return created


def new_claim(project, cid: str, area: str) -> Path:
    if not C.ID_RE.match(cid):
        raise Refused(f"id {cid!r} is not kebab-case ([a-z0-9][a-z0-9-]*)")
    if not project.claims_dir.is_dir():
        raise C.StoreMissing(project.claims_dir)
    p = project.claims_dir / f"{cid}.md"
    if p.exists():
        raise Refused(f"{p} already exists")
    p.write_text(CLAIM_TEMPLATE.format(id=cid, area=quote(area)), encoding="utf-8")
    return p
```

- [ ] **Step 5: Implement `lib/claimlock/cli.py`**

```python
"""Command-line front end. Formatting lives here; logic lives in the modules."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import VERSION
from . import claims as C
from . import ops
from . import project as P

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
```

`build_parser()` returns `(parser, subparsers, add)`; later tasks register commands by calling `add(...)` inside `build_parser` (edit the function body — keep one registration site).

- [ ] **Step 6: Run to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK`; `test_cli_read` contributes 10 tests; total `Ran 43 tests`.

- [ ] **Step 7: Watch the census fail for its own reason**

Temporarily change `sources hashed` to use `0` instead of `hasher.hashed`; run; expected: `test_human_output_and_census` FAILS on the census substring. Re-apply the inverse edit; OK.

- [ ] **Step 8: Commit**

```bash
git add lib/claimlock/ops.py lib/claimlock/cli.py tests/helpers.py tests/test_claims.py tests/test_cli_read.py
git commit -m "feat: cli — init, new, check gate, stale, list, search, show" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
