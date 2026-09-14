### Task 6: git I/O, snapshots, `verify` and `diff`

**Files:**
- Create: `lib/claimlock/gitio.py`, `lib/claimlock/snapshots.py`, `tests/test_gitio.py`, `tests/test_verify_diff.py`
- Modify: `lib/claimlock/ops.py` (add `verify`), `lib/claimlock/cli.py` (add `cmd_verify`, `cmd_diff`, register both in `build_parser`)

**Interfaces:**
- Consumes: `claims.*`, `project.safe_source`, `pins.blob_of_bytes`, `frontmatter.rewrite`, `ops.Refused`, `cli.build_parser/_project/_find/HINT` (Tasks 2–5).
- Produces: `gitio.run/in_git/head/has_blob/cat_blob/changed_paths/head_mark_paths`; `snapshots.store/load/prune`; `ops.verify(project, cid, now=None) -> list[(path, blob)]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_gitio.py`:
```python
import shutil
import unittest
from pathlib import Path

from helpers import TmpCase, git, write
from claimlock import gitio
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


class NoGit(TmpCase):
    def test_everything_degrades_to_empty(self):
        self.assertFalse(gitio.in_git(self.tmp))
        self.assertIsNone(gitio.head(self.tmp))
        self.assertFalse(gitio.has_blob(self.tmp, "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"))
        self.assertIsNone(gitio.cat_blob(self.tmp, "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"))
        self.assertEqual(gitio.changed_paths(self.tmp, None, "HEAD"), [])
        self.assertEqual(gitio.head_mark_paths(self.tmp), [])


@NEED_GIT
class WithGit(TmpCase):
    def setUp(self):
        super().setUp()
        self.top = self.tmp / "top"
        self.top.mkdir()
        git(self.top, "init", "-q", "-b", "main")
        git(self.top, "config", "user.email", "t@example.com")
        git(self.top, "config", "user.name", "t")
        git(self.top, "config", "commit.gpgsign", "false")

    def commit(self, msg):
        git(self.top, "add", "-A")
        git(self.top, "commit", "-q", "-m", msg)
        return git(self.top, "rev-parse", "HEAD").strip()

    def test_head_blob_and_changed_paths_relative_to_a_subdirectory(self):
        self.assertTrue(gitio.in_git(self.top))
        self.assertIsNone(gitio.head(self.top))  # unborn branch
        write(self.top, "proj/a.py", "one\n")
        write(self.top, "other/z.py", "zed\n")
        first = self.commit("one")
        proj = self.top / "proj"
        self.assertEqual(gitio.head(proj), first)
        blob = blob_of_bytes(b"one\n")
        self.assertTrue(gitio.has_blob(proj, blob))
        self.assertEqual(gitio.cat_blob(proj, blob), b"one\n")
        # the root commit: every file under the project dir, relative to it
        self.assertEqual(gitio.changed_paths(proj, None, first), ["a.py"])
        write(self.top, "proj/b.py", "bee\n")
        write(self.top, "other/z.py", "zed2\n")
        second = self.commit("two")
        self.assertEqual(gitio.changed_paths(proj, first, second), ["b.py"])
        # an unreachable "old" falls back to the new commit's own changes
        self.assertEqual(gitio.changed_paths(proj, "0" * 40, second), ["b.py"])

    def test_head_mark_paths_exist_after_a_commit(self):
        write(self.top, "a.py", "one\n")
        self.commit("one")
        marks = gitio.head_mark_paths(self.top)
        self.assertTrue(any(m.endswith("logs/HEAD") for m in marks), marks)
        self.assertTrue(any(m.endswith("refs/heads/main") for m in marks), marks)
        self.assertTrue(all(Path(m).is_absolute() for m in marks))


if __name__ == "__main__":
    unittest.main()
```

`tests/test_verify_diff.py`:
```python
import shutil
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def verifiable(cid, sources=("a.py",)):
    return claim_text(cid, sources=sources, body="The thing holds.\n\nBecause reasons.")


class Verify(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "one\n")

    def test_pins_sets_status_and_preserves_body(self):
        write(self.root, "claims/c.md", verifiable("c"))
        rc, out, err = run_cli(self.root, "verify", "c")
        self.assertEqual(rc, 0, err)
        blob = blob_of_bytes(b"one\n")
        self.assertIn(f"a.py @ {blob[:12]}", out)
        text = (self.root / "claims/c.md").read_text()
        self.assertIn("status: verified\n", text)
        self.assertRegex(text, r"verified_at: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\n")
        self.assertIn(f"  - path: a.py\n    blob: {blob}\n", text)
        self.assertTrue(text.endswith("---\nThe thing holds.\n\nBecause reasons.\n"))
        self.assertEqual(run_cli(self.root, "check")[0], 0)

    def test_refusals_leave_the_file_untouched(self):
        cases = {
            "no-evidence": claim_text("no-evidence", evidence=(), sources=("a.py",)),
            "no-sources": claim_text("no-sources"),
            "refuted": claim_text("refuted", status="refuted", sources=("a.py",)),
            "gone": claim_text("gone", sources=("deleted.py",)),
        }
        for cid, text in cases.items():
            write(self.root, f"claims/{cid}.md", text)
        for cid, text in cases.items():
            with self.subTest(cid):
                rc, _, err = run_cli(self.root, "verify", cid)
                self.assertEqual(rc, 1)
                self.assertIn(cid, err)
                self.assertEqual((self.root / f"claims/{cid}.md").read_text(), text)
        rc, _, err = run_cli(self.root, "verify", "unknown")
        self.assertEqual(rc, 1)
        self.assertIn("no claim 'unknown'", err)

    def test_reverify_prunes_the_old_snapshot(self):
        write(self.root, "claims/c.md", verifiable("c"))
        run_cli(self.root, "verify", "c")
        objects = self.root / ".claimlock" / "objects"
        self.assertEqual([p.name for p in objects.iterdir()], [blob_of_bytes(b"one\n")])
        write(self.root, "a.py", "two!\n")
        run_cli(self.root, "verify", "c")
        self.assertEqual([p.name for p in objects.iterdir()], [blob_of_bytes(b"two!\n")])


class Diff(TmpCase):
    def pin_then_edit(self, root):
        write(root, "a.py", "one\nkeep\n")
        write(root, "claims/c.md", verifiable("c"))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        write(root, "a.py", "two\nkeep\n")

    def test_diff_from_snapshot_without_git(self):
        root = make_repo(self.tmp / "r", use_git=False)
        self.pin_then_edit(root)
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE    c", out)
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, err)
        self.assertIn("-one", out)
        self.assertIn("+two", out)
        self.assertIn("(verified)", out)

    @NEED_GIT
    def test_diff_from_git_object_and_no_snapshot_written(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\nkeep\n")
        git(root, "add", "a.py")
        git(root, "commit", "-q", "-m", "a")
        write(root, "claims/c.md", verifiable("c"))
        run_cli(root, "verify", "c")
        objects = root / ".claimlock" / "objects"
        self.assertFalse(objects.exists() and any(objects.iterdir()))
        write(root, "a.py", "two\nkeep\n")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("+two", out)

    def test_unavailable_prior_content_says_so(self):
        root = make_repo(self.tmp / "r", use_git=False)
        self.pin_then_edit(root)
        shutil.rmtree(root / ".claimlock" / "objects")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0)
        self.assertIn("unavailable", out)

    def test_missing_fresh_and_unknown(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", verifiable("c"))
        run_cli(root, "verify", "c")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("is fresh", out)
        (root / "a.py").unlink()
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("a.py: deleted or renamed", out)
        self.assertEqual(run_cli(root, "diff", "nope")[0], 1)


@NEED_GIT
class Transition(TmpCase):
    def test_pins_survive_git_init(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", verifiable("c"))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "t")
        git(root, "config", "commit.gpgsign", "false")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "adopt")
        self.assertEqual(run_cli(root, "check")[0], 0)
        write(root, "a.py", "two!\n")
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE    c", out)
        self.assertIn("+two!", run_cli(root, "diff", "c")[1])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `ModuleNotFoundError: No module named 'claimlock.gitio'`; `test_verify_diff` tests fail with `invalid choice: 'verify'` (rc 2).

- [ ] **Step 3: Implement `lib/claimlock/gitio.py`**

```python
"""Every git subprocess claimlock runs.

Each function returns None / False / [] when git is not installed, the
directory is not a repository, or the command fails. Git is an enhancement —
it serves prior content for `diff` and reveals commits — never a requirement.
"""
import subprocess
from pathlib import Path


def run(root, *args):
    try:
        return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root,
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def _text(r):
    if r is None or r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").strip()


def in_git(root) -> bool:
    return _text(run(root, "rev-parse", "--is-inside-work-tree")) == "true"


def head(root):
    return _text(run(root, "rev-parse", "--verify", "-q", "HEAD")) or None


def has_blob(root, sha) -> bool:
    r = run(root, "cat-file", "-e", f"{sha}^{{blob}}")
    return r is not None and r.returncode == 0


def cat_blob(root, sha):
    r = run(root, "cat-file", "blob", sha)
    return r.stdout if r is not None and r.returncode == 0 else None


def _lines(out):
    return [l for l in (out or "").splitlines() if l]


def changed_paths(root, old, new) -> list:
    """Paths changed between two commits, relative to `root`, limited to it.

    If `old` is None or unreachable, the paths changed by `new` itself.
    """
    if old:
        out = _text(run(root, "diff", "--name-only", "--relative", "--no-renames", old, new))
        if out is not None:
            return _lines(out)
    out = _text(run(root, "diff-tree", "--no-commit-id", "--name-only", "--relative",
                    "--no-renames", "-r", "--root", new))
    return _lines(out)


def head_mark_paths(root) -> list:
    """Absolute paths whose mtime changes whenever HEAD moves (for a cheap stat gate)."""
    names = ["HEAD", "logs/HEAD", "packed-refs"]
    ref = _text(run(root, "symbolic-ref", "-q", "HEAD"))
    if ref:
        names += [ref, f"logs/{ref}"]
    args = []
    for n in names:
        args += ["--git-path", n]
    out = _text(run(root, "rev-parse", *args))
    return [str((Path(root) / p).resolve()) for p in _lines(out)]
```

- [ ] **Step 4: Implement `lib/claimlock/snapshots.py`**

```python
"""Prior content of pinned files, so `diff` can show what changed.

Git's object store is asked first. When git does not hold a pinned blob (no
repository, or content never committed) `verify` keeps a copy under
`.claimlock/objects/<blob>`. Snapshots are verified on read: a corrupted copy
is treated as unavailable rather than shown as the old content.
"""
import os
import re

from . import gitio
from .pins import blob_of_bytes

BLOB_RE = re.compile(r"^[0-9a-f]{40}$")


def _dir(project):
    return project.state_dir / "objects"


def store(project, blob, data) -> None:
    if not BLOB_RE.match(blob) or gitio.has_blob(project.root, blob):
        return
    p = _dir(project) / blob
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def load(project, blob):
    if not BLOB_RE.match(blob or ""):
        return None
    data = gitio.cat_blob(project.root, blob)
    if data is not None:
        return data
    p = _dir(project) / blob
    if not p.is_file():
        return None
    data = p.read_bytes()
    return data if blob_of_bytes(data) == blob else None


def prune(project, referenced) -> None:
    d = _dir(project)
    if not d.is_dir():
        return
    for f in d.iterdir():
        if f.name not in referenced:
            f.unlink(missing_ok=True)
```

- [ ] **Step 5: Add `verify` to `lib/claimlock/ops.py`**

Add imports at the top: `import os`, `from datetime import datetime`, `from . import frontmatter, snapshots`, `from .pins import blob_of_bytes`, `from .project import safe_source` (keep existing imports). Append:

```python
def verify(project, cid, now=None) -> list:
    """Pin every source of `cid` to its current content and mark it verified.

    Refuses a refuted claim, a claim that would be invalid as verified (no
    evidence, no sources, bad fields), and a claim with a missing source.
    Nothing is written unless every check passes.
    """
    all_claims = C.load_claims(project)
    c = next((x for x in all_claims if x.id == cid), None)
    if c is None:
        raise Refused(f"no claim {cid!r}")
    if c.status == "refuted":
        raise Refused(f"{cid} is refuted; edit its status by hand if it holds again")
    probs = C.problems(c, project, as_status="verified")
    if probs:
        raise Refused(f"{cid} cannot be verified:\n  " + "\n  ".join(probs))
    contents = []
    for s in c.sources:
        try:
            data = safe_source(project.root, s.path).read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise Refused(f"{cid}: source {s.path} does not exist — fix its sources, then verify") from None
        contents.append((s.path, blob_of_bytes(data), data))
    for _, blob, data in contents:
        snapshots.store(project, blob, data)
    stamp = now or datetime.now().astimezone().isoformat(timespec="seconds")
    text = frontmatter.rewrite(c.text, c.path.name, status="verified", verified_at=stamp,
                               sources=[{"path": p, "blob": b} for p, b, _ in contents])
    tmp = c.path.with_name(c.path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, c.path)
    referenced = {b for _, b, _ in contents}
    referenced |= {s.blob for other in all_claims if other.id != cid for s in other.sources if s.blob}
    snapshots.prune(project, referenced)
    return [(p, b) for p, b, _ in contents]
```

- [ ] **Step 6: Add `verify` and `diff` to `lib/claimlock/cli.py`**

Add imports: `import difflib`, `from . import snapshots`. Add:

```python
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
```

Register in `build_parser` after `show`:
```python
    p = add("verify", cmd_verify, "pin sources and mark verified (only after re-checking)")
    p.add_argument("ids", nargs="+")
    p = add("diff", cmd_diff, "show what changed in a claim's sources since it was verified")
    p.add_argument("id")
```

- [ ] **Step 7: Run to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK`. New: `test_gitio` 3, `test_verify_diff` 8 → total `Ran 54 tests`. Report skips (only acceptable without git).

- [ ] **Step 8: Watch two detectors fail for their own reasons**

(a) In `ops.verify`, change `status="verified"` to `status=None`; run; `test_pins_sets_status_and_preserves_body` FAILS on `status: verified`. Revert by re-applying the inverse edit.
(b) Add this test to class `Diff`, run it (PASS), then change `return data if blob_of_bytes(data) == blob else None` in `snapshots.load` to `return data`, run it (FAIL: the diff prints the tampered bytes instead of "unavailable"), and re-apply the inverse edit:
```python
    def test_corrupt_snapshot_is_unavailable(self):
        root = make_repo(self.tmp / "r", use_git=False)
        self.pin_then_edit(root)
        [obj] = list((root / ".claimlock" / "objects").iterdir())
        obj.write_bytes(b"tampered\n")
        self.assertIn("unavailable", run_cli(root, "diff", "c")[1])
```
Expected final: `Ran 55 tests ... OK`.

- [ ] **Step 9: Commit**

```bash
git add lib/claimlock tests/test_gitio.py tests/test_verify_diff.py
git commit -m "feat: verify pins sources; diff shows drift from git objects or snapshots" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
