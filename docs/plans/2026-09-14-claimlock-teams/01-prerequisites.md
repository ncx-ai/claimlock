### Task 1: Prerequisites — ignored-root marker scan; unreadable claims directory

Two false-clean defects open at the v1 merge (spec §7). Both must be fixed before the team gate exists, because a gate that reads clean when it saw nothing is worse than no gate.

Observed 2026-09-14 (git 2.55): from a store root inside a directory an enclosing repo ignores, `git check-ignore -q .` exits **0**, and `git ls-files --cached --others --exclude-standard` lists **0** files; from a non-ignored subdirectory and at the top level it exits **1**.

**Files:**
- Modify: `lib/claimlock/gitio.py` (add `root_is_ignored`), `lib/claimlock/refs.py` (`_files`), `lib/claimlock/claims.py` (`StoreUnreadable`, `load_claims`)
- Test: `tests/test_gitio.py`, `tests/test_refs_import_selftest.py`, `tests/test_cli_read.py`

**Interfaces:**
- Consumes: existing `gitio.run`, `gitio.ls_files`, `claims.StoreMissing` (mapped to exit 2 in `cli.main`).
- Produces: `gitio.root_is_ignored(root) -> bool`; `claims.StoreUnreadable(StoreMissing)` raised by `load_claims` when the claims directory exists but cannot be listed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gitio.py` (inside the existing `WithGit` class add the first method; add the second to `NoGit`):
```python
    def test_root_is_ignored_only_inside_an_ignored_directory(self):
        write(self.top, ".gitignore", "scratch/\n")
        (self.top / "scratch" / "proj").mkdir(parents=True)
        (self.top / "open").mkdir()
        self.assertTrue(gitio.root_is_ignored(self.top / "scratch" / "proj"))
        self.assertFalse(gitio.root_is_ignored(self.top / "open"))
        self.assertFalse(gitio.root_is_ignored(self.top))
```
```python
    def test_root_is_ignored_is_false_outside_git(self):
        self.assertFalse(gitio.root_is_ignored(self.tmp))
```

Append to `tests/test_refs_import_selftest.py` (add `import shutil` and `from helpers import git` to its imports if missing):
```python
@unittest.skipIf(shutil.which("git") is None, "git not installed")
class IgnoredRoot(TmpCase):
    def outer(self, ignore):
        outer = self.tmp / "outer"
        outer.mkdir()
        git(outer, "init", "-q", "-b", "main")
        write(outer, ".gitignore", ignore)
        return outer

    def test_store_inside_an_ignored_directory_is_still_scanned(self):
        outer = self.outer("scratch/\n")
        root = make_repo(outer / "scratch" / "proj", use_git=False)
        write(root, "docs/x.md", "Claim: `ghost`\n")
        markers, files = refs.scan(load(root))
        self.assertEqual((files, [m.id for m in markers]), (1, ["ghost"]))
        rc, out, _ = run_cli(root, "refs")
        self.assertEqual(rc, 1, out)
        self.assertIn("1 dangling", out)

    def test_a_non_ignored_store_still_honours_gitignore(self):
        outer = self.outer("target/\n")
        root = make_repo(outer / "proj", use_git=False)
        write(root, "target/t.md", "Claim: `in-ignored-tree`\n")
        write(root, "docs/ok.md", "Claim: `in-open-tree`\n")
        markers, _ = refs.scan(load(root))
        self.assertEqual([m.id for m in markers], ["in-open-tree"])
```

Append to `tests/test_cli_read.py` (ensure `import os` and `import unittest` are imported):
```python
class UnreadableClaimsDir(TmpCase):
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read any directory")
    def test_unreadable_claims_dir_is_exit_2_not_a_clean_store(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "claims/c.md", claim_text("c"))
        os.chmod(root / "claims", 0)
        try:
            rc, out, err = run_cli(root, "check")
        finally:
            os.chmod(root / "claims", 0o755)
        self.assertEqual(rc, 2, out)
        self.assertIn("cannot be read", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("0 claims", out)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_gitio.py" -v`, then `-p "test_refs_import_selftest.py"`, then `-p "test_cli_read.py"`.
Expected: `AttributeError: module 'claimlock.gitio' has no attribute 'root_is_ignored'`; `IgnoredRoot.test_store_inside_an_ignored_directory_is_still_scanned` fails with `(0, []) != (1, ['ghost'])`; `UnreadableClaimsDir` fails with `0 != 2` (the store reads as clean). `test_a_non_ignored_store_still_honours_gitignore` should already pass — it is the control proving the fix does not disable gitignore handling.

- [ ] **Step 3: Implement**

`lib/claimlock/gitio.py` — add after `in_git`:
```python
def root_is_ignored(root) -> bool:
    """True when `root` itself lies inside a directory an enclosing repository
    ignores. `git ls-files` then lists nothing under it, so an empty listing
    there means "git will not tell us", not "there are no files"."""
    r = run(root, "check-ignore", "-q", ".")
    return r is not None and r.returncode == 0
```

`lib/claimlock/refs.py` — in `_files`, replace `listed = gitio.ls_files(project.root)` with:
```python
    # A store inside a directory an enclosing repo ignores gets an empty
    # listing from git; walk instead of silently scanning nothing.
    listed = None if gitio.root_is_ignored(project.root) else gitio.ls_files(project.root)
```
and extend the module docstring's last sentence with: "— except when the store root is itself ignored by an enclosing repository, where the tree is walked."

`lib/claimlock/claims.py` — add `import os` to the imports; add after `StoreMissing`:
```python
class StoreUnreadable(StoreMissing):
    """The claims directory exists but cannot be listed. A subclass of
    StoreMissing so every caller that maps "no readable store" to exit 2
    handles it without change; reading it as an empty store would be a
    false clean."""

    def __init__(self, path, error):
        Exception.__init__(self, f"claims directory {path} cannot be read: "
                                 f"{getattr(error, 'strerror', None) or error}")
        self.path = path
```
In `load_claims`, replace the `for p in sorted(project.claims_dir.glob("*.md")):` loop header (and its README skip) with:
```python
    try:
        names = sorted(os.listdir(project.claims_dir))
    except OSError as e:
        raise StoreUnreadable(project.claims_dir, e) from None
    for name in names:
        if not name.endswith(".md") or name == "README.md":
            continue
        p = project.claims_dir / name
```
(`Path.glob` on an unreadable directory silently yields nothing; `os.listdir` raises.) Keep the rest of the loop body unchanged.

- [ ] **Step 4: Run to verify they pass**

Run the three focused commands from Step 2 — all pass. Then the full suite: `python3 -m unittest discover -s tests -v` and `python3 -W error::ResourceWarning -m unittest discover -s tests`. Expected: OK, 122 + 5 = **127** tests (report the observed number), 0 skipped (as non-root with git).

- [ ] **Step 5: Watch each fix fail for its own reason**

(a) In `refs._files` temporarily restore `listed = gitio.ls_files(project.root)`; `IgnoredRoot.test_store_inside_an_ignored_directory_is_still_scanned` fails. Re-apply the inverse edit.
(b) In `load_claims` temporarily wrap the `os.listdir` call as `names = sorted(os.listdir(project.claims_dir)) if os.access(project.claims_dir, os.R_OK) else []`; `UnreadableClaimsDir` fails with rc 0. Re-apply the inverse edit. Suite green.

- [ ] **Step 6: Commit**

```bash
git add lib/claimlock/gitio.py lib/claimlock/refs.py lib/claimlock/claims.py tests/test_gitio.py tests/test_refs_import_selftest.py tests/test_cli_read.py
git commit -m "fix: an ignored store root is still scanned; an unreadable claims dir is exit 2" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
