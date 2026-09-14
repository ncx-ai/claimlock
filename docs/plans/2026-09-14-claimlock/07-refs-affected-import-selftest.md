### Task 7: `refs`, `affected`, `import`, `self-test`

**Files:**
- Create: `lib/claimlock/refs.py`, `lib/claimlock/importer.py`, `lib/claimlock/selftest.py`, `tests/test_refs_import_selftest.py`
- Modify: `lib/claimlock/cli.py` (add `cmd_refs`, `cmd_affected`, `cmd_import`, `cmd_self_test`; register in `build_parser`)

**Interfaces:**
- Consumes: `project.load/is_within/CONFIG`, `claims.*`, `ops.verify/Refused`, `pins.Hasher`, `frontmatter.*`, cli helpers (Tasks 2–6).
- Produces: `refs.Marker(path, line, id)`, `refs.scan(project, only=None) -> (markers, files_scanned)`; `importer.import_dir(project, src) -> (ids, errors)`; `selftest.run(out=print) -> int`. Task 8 uses `refs.scan(project, only=set_of_root_relative_paths)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_refs_import_selftest.py`:
```python
import io
import shutil
import unittest
from unittest import mock

from helpers import TmpCase, claim_text, make_repo, run_cli, write
from claimlock import refs, selftest
from claimlock.project import load


class Refs(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "claims/a.md", claim_text("a", body="Mentions Claim: `zzz` in a claim body."))
        write(self.root, "README.md", "Holds. Claim: `a`\n")
        write(self.root, "docs/x.md", "text\nClaim: `ghost` and Claim: `a`\n")
        write(self.root, ".hidden/y.md", "Claim: `ghost2`\n")
        write(self.root, "node_modules/pkg/z.md", "Claim: `ghost3`\n")
        write(self.root, "notes.txt", "Claim: `ghost4`\n")

    def test_scan_scope_and_census(self):
        markers, files = refs.scan(load(self.root))
        self.assertEqual(files, 2)
        self.assertEqual([(m.path, m.line, m.id) for m in markers],
                         [("README.md", 1, "a"), ("docs/x.md", 2, "ghost"), ("docs/x.md", 2, "a")])

    def test_only_limits_to_given_paths(self):
        markers, files = refs.scan(load(self.root), only={"docs/x.md", "notes.txt", "missing.md"})
        self.assertEqual((files, [m.id for m in markers]), (1, ["ghost", "a"]))

    def test_cli_reports_dangling(self):
        rc, out, _ = run_cli(self.root, "refs")
        self.assertEqual(rc, 1)
        self.assertIn("DANGLING docs/x.md:2  Claim `ghost` names no claim", out)
        self.assertIn("claimlock: 3 markers in 2 files scanned, 1 dangling", out)
        (self.root / "docs/x.md").write_text("Claim: `a`\n")
        rc, out, _ = run_cli(self.root, "refs")
        self.assertEqual(rc, 0)
        self.assertIn("2 markers in 2 files scanned, 0 dangling", out)

    def test_custom_globs_and_pattern(self):
        root = make_repo(self.tmp / "c", use_git=False,
                         config='marker_globs = ["*.txt"]\nmarker_pattern = "see claim ([a-z-]+)"\n')
        write(root, "n.txt", "see claim nope\n")
        write(root, "n.md", "see claim nope\n")
        markers, files = refs.scan(load(root))
        self.assertEqual((files, [m.id for m in markers]), (1, ["nope"]))


class Affected(TmpCase):
    def test_lists_claims_citing_a_path_from_any_cwd(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=("src/a.py",)))
        write(root, "claims/d.md", claim_text("d", sources=("src/b.py",)))
        rc, out, _ = run_cli(root, "affected", "src/a.py")
        self.assertEqual((rc, out.split("\t")[0]), (0, "c"))
        rc, out, _ = run_cli(root / "src", "affected", "a.py")
        self.assertEqual(out.split("\t")[0], "c")
        rc, out, _ = run_cli(root, "affected", "nothing.py")
        self.assertEqual((rc, out), (0, ""))


ORIGIN = """---
id: {id}
area: api
status: verified
verified_at: 2026-09-08T10:00:00-04:00
evidence:
  - kind: test
    ref: api::tests::timeout_is_clamped
sources:
  - src/engine.py
  - src/limits.py
---
The per-attempt timeout is clamped to the configured maximum.
"""


class Import(TmpCase):
    def test_imports_as_unpinned_and_keeps_everything_else(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/engine.py", "e\n")
        write(root, "src/limits.py", "l\n")
        old = self.tmp / "old"
        write(old, "timeout-clamped.md", ORIGIN.format(id="timeout-clamped"))
        write(old, "broken.md", "---\nid: broken\nsources: [x]\n---\nb\n")
        write(old, "README.md", "# old store\n")
        write(root, "claims/exists.md", claim_text("exists"))
        write(old, "exists.md", ORIGIN.format(id="exists"))
        rc, out, err = run_cli(root, "import", str(old))
        self.assertEqual(rc, 1)  # errors were reported
        self.assertIn("imported 1 claim", out)
        self.assertIn("broken.md:3", err)
        self.assertIn("exists.md", err)
        text = (root / "claims/timeout-clamped.md").read_text()
        self.assertIn("sources:\n  - path: src/engine.py\n  - path: src/limits.py\n---", text)
        self.assertIn("ref: api::tests::timeout_is_clamped", text)
        self.assertIn("verified_at: 2026-09-08T10:00:00-04:00", text)
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("UNPINNED timeout-clamped", out)


class SelfTest(TmpCase):
    def test_passes_on_a_working_build(self):
        buf = io.StringIO()
        rc = selftest.run(out=lambda s="": buf.write(s + "\n"))
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("edited source: expected 'stale', got 'stale'", buf.getvalue())
        if shutil.which("git"):
            self.assertIn("[git repository]", buf.getvalue())

    def test_fails_when_staleness_detection_is_broken(self):
        buf = io.StringIO()
        with mock.patch("claimlock.selftest.C.freshness", return_value=("fresh", [])):
            rc = selftest.run(out=lambda s="": buf.write(s + "\n"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", buf.getvalue())

    def test_cli(self):
        rc, out, _ = run_cli(self.tmp, "self-test")
        self.assertEqual(rc, 0, out)
        self.assertIn("SELF-TEST: all", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: `ModuleNotFoundError: No module named 'claimlock.refs'`.

- [ ] **Step 3: Implement `lib/claimlock/refs.py`**

```python
"""Claim markers in prose — ``Claim: `id` `` written beside the sentence it pins.

A marker naming no claim reads as pinned and pins nothing, which is worse than
no marker: nobody goes back to check a sentence that looks covered.

Globs use fnmatch semantics (`*` crosses directories); a leading `**/` also
matches at the root. Hidden directories, node_modules and the claims
directory are never scanned.
"""
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from .project import is_within

SKIP_DIRS = {"node_modules"}


@dataclass
class Marker:
    path: str
    line: int
    id: str


def _matches(rel, globs):
    return any(fnmatch.fnmatchcase(rel, g) or (g.startswith("**/") and fnmatch.fnmatchcase(rel, g[3:]))
               for g in globs)


def _excluded(project, rel):
    parts = rel.split("/")
    if any(p.startswith(".") or p in SKIP_DIRS for p in parts[:-1]):
        return True
    return is_within((project.root / rel).resolve(), project.claims_dir)


def _files(project, only):
    if only is not None:
        for rel in sorted(only):
            p = project.root / rel
            if p.is_file() and _matches(rel, project.marker_globs) and not _excluded(project, rel):
                yield p, rel
        return
    for dirpath, dirnames, filenames in os.walk(project.root):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames
                             if not n.startswith(".") and n not in SKIP_DIRS
                             and not is_within((d / n).resolve(), project.claims_dir))
        for f in sorted(filenames):
            rel = (d / f).relative_to(project.root).as_posix()
            if _matches(rel, project.marker_globs):
                yield d / f, rel


def scan(project, only=None):
    """(markers, files_scanned). `only` limits the scan to those root-relative paths."""
    markers, scanned = [], 0
    for p, rel in _files(project, only):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for n, line in enumerate(text.splitlines(), 1):
            for m in project.marker_pattern.finditer(line):
                markers.append(Marker(rel, n, m.group(1)))
    return markers, scanned
```

Note on `test_scan_scope_and_census`: `os.walk` yields root files first (`README.md`), then `docs/x.md`, so marker order is as asserted.

- [ ] **Step 4: Implement `lib/claimlock/importer.py`**

```python
"""Import claims written in the original ground-truth format.

That format lists `sources` as plain paths and has no pins. Every imported
claim therefore arrives UNPINNED, and `check` fails until each one is
re-checked and verified. That is deliberate: an import must not launder old
verifications into fresh pins. Everything except the sources block is copied
byte-for-byte.
"""
from pathlib import Path

from . import frontmatter
from .claims import StoreMissing


def import_dir(project, src):
    if not project.claims_dir.is_dir():
        raise StoreMissing(project.claims_dir)
    imported, errors = [], []
    for p in sorted(Path(src).glob("*.md")):
        if p.name == "README.md":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            fm, _ = frontmatter.split(text, p.name)
            meta = frontmatter.parse(fm, p.name)
        except frontmatter.FrontmatterError as e:
            errors.append(f"skipped {e}")
            continue
        dest = project.claims_dir / p.name
        if dest.exists():
            errors.append(f"skipped {p.name}: {dest} already exists")
            continue
        raw = meta.get("sources") or []
        paths = []
        for e in raw if isinstance(raw, list) else []:
            if isinstance(e, str):
                paths.append(e)
            elif isinstance(e, dict) and isinstance(e.get("path"), str):
                paths.append(e["path"])
        dest.write_text(frontmatter.rewrite(text, p.name, sources=[{"path": x} for x in paths]),
                        encoding="utf-8")
        imported.append(p.stem)
    return imported, errors
```

- [ ] **Step 5: Implement `lib/claimlock/selftest.py`**

```python
"""Prove the detectors can fire.

A gate nobody has watched fail is decoration. This builds a throwaway store —
in a plain directory and, when git is available, in a repository — pins a
source, then edits it, deletes it, and plants a dangling marker, requiring
each detector to report. It exercises the real code paths, not the store in
the current project.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import claims as C
from . import ops, refs
from . import project as P
from .pins import Hasher

PROBE = """---
id: probe
area: self-test
status: unverified
evidence:
  - kind: run
    ref: claimlock self-test
sources:
  - src.txt
---
The probe source is unchanged.
"""


def run(out=print) -> int:
    results = []

    def expect(label, got, want):
        ok = got == want
        results.append(ok)
        out(f"  {'ok  ' if ok else 'FAIL'}  {label}: expected {want!r}, got {got!r}")

    with tempfile.TemporaryDirectory() as d:
        arms = [("plain directory", False)]
        if shutil.which("git"):
            arms.append(("git repository", True))
        else:
            out("  skip  git arm: git is not on PATH (the plain-directory arm still ran)")
        for label, use_git in arms:
            root = Path(d) / ("git" if use_git else "plain")
            root.mkdir()
            if use_git:
                for args in (["init", "-q"], ["config", "user.email", "self@test"], ["config", "user.name", "self"]):
                    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            (root / P.CONFIG).write_text("")
            (root / "claims").mkdir()
            (root / "src.txt").write_text("before\n")
            (root / "claims" / "probe.md").write_text(PROBE)
            project = P.load(root)
            ops.verify(project, "probe")

            def state():
                c = next(x for x in C.load_claims(project) if x.id == "probe")
                return C.freshness(c, project, Hasher(root, None))[0]

            expect(f"[{label}] pinned source", state(), "fresh")
            (root / "src.txt").write_text("after, and a different length\n")
            expect(f"[{label}] edited source", state(), "stale")
            (root / "src.txt").unlink()
            expect(f"[{label}] deleted source", state(), "missing")
            (root / "doc.md").write_text("Claim: `no-such-claim`\nClaim: `probe`\n")
            markers, _ = refs.scan(project)
            ids = {c.id for c in C.load_claims(project)}
            expect(f"[{label}] dangling markers", sorted(m.id for m in markers if m.id not in ids),
                   ["no-such-claim"])

    out("")
    failed = results.count(False)
    if failed:
        out(f"SELF-TEST: {failed} of {len(results)} checks FAILED — every green result from this build is meaningless")
        return 1
    out(f"SELF-TEST: all {len(results)} checks passed")
    return 0
```

- [ ] **Step 6: Add the commands to `lib/claimlock/cli.py`**

Add imports: `from . import importer, refs, selftest`. Add:
```python
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
    base = Path(args.dir) if args.dir else Path.cwd()
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
    ids, errors = importer.import_dir(project, Path(args.src))
    for e in errors:
        print(f"claimlock: {e}", file=sys.stderr)
    noun = "claim" if len(ids) == 1 else "claims"
    print(f"imported {len(ids)} {noun}; each is UNPINNED until re-checked and verified")
    return 1 if errors else 0


def cmd_self_test(args):
    return selftest.run()
```
Register after `diff`:
```python
    add("refs", cmd_refs, "fail on Claim markers that name no claim")
    p = add("affected", cmd_affected, "claims whose sources include these paths")
    p.add_argument("paths", nargs="+")
    p = add("import", cmd_import, "import claims from the original ground-truth format")
    p.add_argument("src")
    add("self-test", cmd_self_test, "prove the detectors can fail")
```

- [ ] **Step 7: Run to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK`; new tests: Refs 4, Affected 1, Import 1, SelfTest 3 = 9; total `Ran 64 tests`.

- [ ] **Step 8: Watch the exclusion rule fail for its own reason**

Temporarily remove `and not is_within((d / n).resolve(), project.claims_dir)` from `_files`; run; expected: `test_scan_scope_and_census` FAILS (a `zzz` marker from `claims/a.md` appears, files == 3). Re-apply the inverse edit; OK.

- [ ] **Step 9: Commit**

```bash
git add lib/claimlock tests/test_refs_import_selftest.py
git commit -m "feat: refs, affected, import and a self-test that proves detectors fire" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
