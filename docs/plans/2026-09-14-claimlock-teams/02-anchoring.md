### Task 2: Anchoring — `unanchored`, anchor set from history + index, snapshots removed

Spec §4.2, §4.4, §4.5 and amendments T1–T2. A pin is **anchored** when its blob appears at that source path in any commit reachable from any ref, or is the blob staged in the index for that path. Anchoring is evaluated only inside git.

Observed 2026-09-14 (git 2.55): `git rev-list --objects --all -- f.txt` run from subdirectory `sub/` prints paths relative to the **repository top level** (`b680253… sub/f.txt`); `git rev-parse --show-prefix` prints `sub/`; `git ls-files -s -- f.txt` from `sub/` prints `100644 b680253… 0\tf.txt` (cwd-relative). `git cat-file -e` reports a blob written by `hash-object -w`, and a staged-then-unstaged blob, as present although neither is in any commit — so it cannot be the anchor test.

**Files:**
- Modify: `lib/claimlock/gitio.py` (add `anchored_blobs`), `lib/claimlock/claims.py` (`NON_FRESH`, `_SEVERITY`, `anchors_for`, `freshness`, `evaluate`), `lib/claimlock/ops.py` (`verify`: remove snapshots), `lib/claimlock/cli.py` (`cmd_diff`, `HINT`, remove `snapshots` import), `lib/claimlock/selftest.py` (git arm)
- Delete: `lib/claimlock/snapshots.py`
- Create: `tests/test_anchoring.py`
- Modify tests: `tests/test_verify_diff.py`, and any existing test that now observes `unanchored` by design (see Step 4)

**Interfaces:**
- Consumes: `gitio.run/_text/cat_blob`, `claims.Result`, `project.safe_source`.
- Produces: `gitio.anchored_blobs(root, rels) -> set[str] | None`; `claims.NON_FRESH = ("unpinned", "unanchored", "stale", "missing")`; `claims.anchors_for(project, paths) -> set | None`; `claims.freshness(claim, project, hasher, anchors=None)`; `evaluate` computes anchors once per call. Later tasks call `C.anchors_for` wherever they call `freshness` directly.

- [ ] **Step 1: Write the failing tests**

`tests/test_anchoring.py`:
```python
"""A pin is anchored when every clone can recover its content from git:
it appears at that path in a reachable commit, or it is staged right now."""
import json
import shutil
import subprocess
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def state_of(root, cid="c"):
    rc, out, err = run_cli(root, "check", "--json")
    data = json.loads(out)
    return {r["id"]: r["state"] for r in data["results"]}[cid]


@NEED_GIT
class Anchoring(TmpCase):
    def store(self, root=None):
        root = root or make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        return root

    def test_verified_unstaged_is_unanchored_until_staged(self):
        root = self.store()
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual(state_of(root), "unanchored")
        git(root, "add", "a.py")
        self.assertEqual(state_of(root), "fresh")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        self.assertEqual(state_of(root), "fresh")

    def test_content_from_an_older_commit_stays_anchored(self):
        root = self.store()
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "one")
        write(root, "a.py", "two\n")
        git(root, "commit", "-qam", "two")
        write(root, "a.py", "one\n")  # back to the verified content, uncommitted
        self.assertEqual(state_of(root), "fresh")

    def test_a_blob_written_but_never_committed_is_unanchored(self):
        root = self.store()
        write(root, "a.py", "loose\n")
        subprocess.run(["git", "hash-object", "-w", "a.py"], cwd=root, check=True, capture_output=True)
        run_cli(root, "verify", "c")
        self.assertEqual(state_of(root), "unanchored")

    def test_a_staged_then_unstaged_blob_is_unanchored(self):
        root = self.store()
        write(root, "a.py", "staged\n")
        git(root, "add", "a.py")
        git(root, "reset", "-q", "a.py")
        run_cli(root, "verify", "c")
        self.assertEqual(state_of(root), "unanchored")

    def test_store_in_a_subdirectory_of_the_repository(self):
        outer = self.tmp / "outer"
        outer.mkdir()
        git(outer, "init", "-q", "-b", "main")
        git(outer, "config", "user.email", "t@example.com")
        git(outer, "config", "user.name", "t")
        git(outer, "config", "commit.gpgsign", "false")
        root = self.store(make_repo(outer / "proj", use_git=False))
        run_cli(root, "verify", "c")
        git(outer, "add", "-A")
        git(outer, "commit", "-qm", "sub")
        self.assertEqual(state_of(root), "fresh")

    def test_diff_explains_an_unanchored_pin(self):
        root = self.store()
        run_cli(root, "verify", "c")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0)
        self.assertIn("never committed or staged", out)

    def test_diff_reads_prior_content_from_git(self):
        root = self.store()
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        write(root, "a.py", "two\n")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("-one", out)
        self.assertIn("+two", out)


class OutsideGit(TmpCase):
    def test_never_unanchored_and_diff_says_unavailable(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        run_cli(root, "verify", "c")
        self.assertEqual(state_of(root), "fresh")
        self.assertFalse((root / ".claimlock" / "objects").exists())
        write(root, "a.py", "two\n")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("unavailable", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -p "test_anchoring.py" -v`
Expected: the `unanchored` expectations fail (`'fresh' != 'unanchored'`), `test_diff_explains_an_unanchored_pin` fails, and `OutsideGit` fails on `.claimlock/objects` existing. `test_content_from_an_older_commit_stays_anchored`, `test_store_in_a_subdirectory_of_the_repository` and `test_diff_reads_prior_content_from_git` may already pass — they are controls.

- [ ] **Step 3: Implement**

`lib/claimlock/gitio.py` — add:
```python
def _prefix(root):
    """`root`'s path inside its repository ('' at the top level, 'sub/' below
    it), or None outside git. `rev-list --objects` prints top-level paths."""
    return _text(run(root, "rev-parse", "--show-prefix"))


def anchored_blobs(root, rels):
    """Blob ids every clone can recover for these root-relative paths: every
    blob that appeared at one of them in any commit reachable from any ref,
    plus the blob currently staged for each. None outside git or on failure.

    `cat-file -e` is not used: it also reports loose objects that no commit
    references (written by `hash-object -w`, or staged then unstaged)."""
    if not rels:
        return set()
    prefix = _prefix(root)
    if prefix is None:
        return None
    r = run(root, "rev-list", "--objects", "--all", "--", *rels)
    if r is None or r.returncode != 0:
        return None
    wanted = {prefix + rel for rel in rels}
    blobs = set()
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        sha, _, path = line.partition(" ")
        if path in wanted:
            blobs.add(sha)
    s = run(root, "ls-files", "-s", "-z", "--", *rels)
    if s is not None and s.returncode == 0:
        for rec in s.stdout.split(b"\0"):
            meta, tab, _ = rec.partition(b"\t")
            parts = meta.split()
            if tab and len(parts) >= 2:
                blobs.add(parts[1].decode("ascii", "replace"))
    return blobs
```

`lib/claimlock/claims.py`:
```python
NON_FRESH = ("unpinned", "unanchored", "stale", "missing")
_SEVERITY = {"fresh": 0, "unpinned": 1, "unanchored": 2, "stale": 3, "missing": 4}
```
Add `from . import gitio` to imports. Add:
```python
def anchors_for(project, paths):
    """The anchor set for these source paths (see gitio.anchored_blobs), or
    None outside git — where anchoring is not evaluated at all."""
    paths = sorted({p for p in paths if safe_source(project.root, p) is not None})
    if not paths:
        return set()
    return gitio.anchored_blobs(project.root, paths)
```
Change `freshness` to `def freshness(claim, project, hasher, anchors=None):`, update its docstring precedence to `missing > stale > unanchored > unpinned > fresh`, and replace the final `else: st = "fresh"` with:
```python
        elif anchors is not None and s.blob not in anchors:
            st = "unanchored"
        else:
            st = "fresh"
```
Replace `evaluate`:
```python
def evaluate(project, hasher):
    claims = load_claims(project)
    anchors = anchors_for(project, [s.path for c in claims
                                    if c.status == "verified" and not c.parse_error
                                    for s in c.sources])
    return [Result(c, problems(c, project), *freshness(c, project, hasher, anchors))
            for c in claims]
```

`lib/claimlock/ops.py` — remove `snapshots` from the import line, delete the `for _, blob, data in contents: snapshots.store(...)` loop and the two `referenced`/`snapshots.prune` lines at the end of `verify`. Keep everything else in `verify` unchanged in this task.

Delete `lib/claimlock/snapshots.py` (`git rm lib/claimlock/snapshots.py`).

`lib/claimlock/cli.py` — remove `from . import snapshots`; add `from . import gitio`. Add to `HINT`:
```python
    "unanchored": ("the pinned content was never committed or staged — commit the source so every "
                   "clone can see it (if it changed since, re-check, then: claimlock verify {id})"),
```
In `cmd_diff`, replace `state, per = C.freshness(c, project, hasher)` with:
```python
    state, per = C.freshness(c, project, hasher, C.anchors_for(project, [s.path for s in c.sources]))
```
and inside the per-source loop add, before the `old = …` line:
```python
        if st == "unanchored":
            print(f"--- {path}: unchanged since verification, but that content was never committed "
                  f"or staged — commit it so other clones can diff this claim")
            continue
```
replace `old = snapshots.load(project, pins[path])` with `old = gitio.cat_blob(project.root, pins[path])`, and replace the "unavailable" message with:
```python
            print(f"--- {path}: changed, but the pinned content {pins[path][:12]} is not in git "
                  f"(never committed, or no repository) — prior content unavailable; "
                  f"re-read the claim against the current file")
```

`lib/claimlock/selftest.py` — read it first. In the per-arm body, after `ops.verify(project, "probe")`, change the `state()` helper to pass anchors:
```python
            def state():
                c = next(x for x in C.load_claims(project) if x.id == "probe")
                return C.freshness(c, project, Hasher(root, None),
                                   C.anchors_for(project, [s.path for s in c.sources]))[0]
```
and replace the single `expect(f"[{label}] pinned source", state(), "fresh")` with:
```python
            if use_git:
                expect(f"[{label}] verified, unstaged source", state(), "unanchored")
                subprocess.run(["git", "add", "src.txt"], cwd=root, check=True, capture_output=True)
            expect(f"[{label}] pinned source", state(), "fresh")
```
Update the module docstring to mention the unanchored check.

- [ ] **Step 4: Update the tests this change legitimately breaks**

1. `tests/test_verify_diff.py`: delete every test that asserts snapshot behaviour — `test_reverify_prunes_the_old_snapshot`, `test_diff_from_snapshot_without_git`, `test_diff_from_git_object_and_no_snapshot_written`, `test_unavailable_prior_content_says_so`, `test_corrupt_snapshot_is_unavailable`, and the `SnapshotWrite` class (their replacements are in `tests/test_anchoring.py`). Ensure the `Transition` class carries `@NEED_GIT` directly above it (a prior edit left the decorator on the wrong class).
2. The `check` census now includes the new state, in `NON_FRESH` order: `— N invalid, N unpinned, N unanchored, N stale, N missing`, and `check --json`'s `counts` gains `"unanchored"`. Update the exact-string and exact-dict assertions in `tests/test_cli_read.py` (`test_human_output_and_census`, `test_json`, `test_area_filter_and_clean_exit`, and any other census assertion the suite reports) to include `0 unanchored` / `"unanchored": 0` in that position — nothing else about those assertions changes.
3. Run the full suite. Any other failure must be one of: a test that runs `check`, `stale` or a hook between `verify` and `git add` inside a git repo and now observes `unanchored`. Fix each such test by staging (`git add`) before the assertion — do not weaken an assertion — and list every test you changed, with the reason, in your report. Any failure of a different kind is a real defect: stop and report it.

- [ ] **Step 5: Run to verify everything passes**

Run: `python3 -m unittest discover -s tests -p "test_anchoring.py" -v`, then the full suite plain and with `-W error::ResourceWarning`, then `python3 bin/claimlock self-test` (expect `SELF-TEST: all 9 checks passed` when git is installed). Report the observed test total.

- [ ] **Step 6: Watch anchoring fail for its own reason**

(a) In `gitio.anchored_blobs` temporarily drop the `ls-files -s` block; `test_verified_unstaged_is_unanchored_until_staged` fails at the staged assertion. Re-apply the inverse edit.
(b) Temporarily change `wanted = {prefix + rel …}` to `wanted = set(rels)`; `test_store_in_a_subdirectory_of_the_repository` fails. Re-apply. Suite green.

- [ ] **Step 7: Commit**

```bash
git add -A lib/claimlock tests
git commit -m "feat: unanchored pins; prior content comes from git, snapshots removed" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
(`git add -A lib/claimlock tests` stages the deletion of `snapshots.py`; check `git status` shows nothing else staged.)
