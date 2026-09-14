### Task 3: Normalized hashing — pins every clone agrees on

Spec §4.3 and amendment T5. Inside a git work tree a source's pin is the blob git would store for it (clean filters, `text`/`eol`/`core.autocrlf` applied), so an LF checkout and a CRLF checkout of the same commit agree. Outside git, raw bytes as today.

Observed 2026-09-14 (git 2.55): a file committed as `814f4a42…` checked out with CRLF in a `core.autocrlf=true` clone hashes to `814f4a42…` via `git hash-object --stdin-paths` given an **absolute** path (and via `--path`), from a subdirectory. Relative paths given to `--stdin-paths` are resolved from the repository **top level**, not the cwd. One path that cannot be opened aborts the whole batch (`fatal: could not open …`, rc 128) after printing the hashes before it.

**Files:**
- Modify: `lib/claimlock/gitio.py` (add `hash_paths`), `lib/claimlock/pins.py` (`Hasher` mode, `prime`, `blob(use_cache=)`, mode-tagged cache entries), `lib/claimlock/claims.py` (`open_hasher`, `evaluate` primes), `lib/claimlock/ops.py` (`verify` hashes through the hasher, cache bypassed)
- Create: `tests/test_hashing.py`

**Interfaces:**
- Consumes: `gitio.in_git`, `claims.evaluate/anchors_for` (Task 2).
- Produces: `gitio.hash_paths(root, rels) -> dict | None`; `pins.Hasher(root, cache_path, mode="raw"|"git")` with `.mode`, `.prime(rels)`, `.blob(rel, use_cache=True)`; `claims.open_hasher(project)` returns a git-mode hasher inside a work tree. Tasks 5 (`resolve`) and 7 (hooks) use `hasher.blob(path, use_cache=False)` / `open_hasher`.

- [ ] **Step 1: Write the failing tests**

`tests/test_hashing.py`:
```python
"""Pins inside git are the blob git stores, so clones with different line-ending
settings agree; outside git a pin is the file's raw bytes."""
import os
import re
import shutil
import subprocess
import time
import unittest
from unittest import mock

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write
from claimlock import pins
from claimlock.pins import Hasher, blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")
OLD = time.time_ns() - 100 * 1_000_000_000


def clone(bare, dest, *config):
    subprocess.run(["git", *config, "clone", "-q", str(bare), str(dest)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(dest, "config", k, v)


@NEED_GIT
class LineEndingsAcrossClones(TmpCase):
    def test_lf_and_crlf_clones_agree_on_every_pin(self):
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True, capture_output=True)
        a = self.tmp / "a"
        clone(bare, a)
        git(a, "config", "core.autocrlf", "false")
        run_cli(a, "init")
        write(a, "src/limit.py", "MAX = 5\n\ndef clamp(n):\n    return min(n, MAX)\n")
        write(a, "claims/c.md", claim_text("c", sources=("src/limit.py",)))
        git(a, "add", "-A")
        self.assertEqual(run_cli(a, "verify", "c")[0], 0)
        git(a, "add", "-A")
        git(a, "commit", "-qm", "verified on LF")
        git(a, "push", "-q", "origin", "main")

        b = self.tmp / "b"
        clone(bare, b, "-c", "core.autocrlf=true")
        git(b, "config", "core.autocrlf", "true")
        (b / "src" / "limit.py").unlink()
        git(b, "checkout", "--", "src/limit.py")
        self.assertIn(b"\r\n", (b / "src" / "limit.py").read_bytes(), "precondition: B holds CRLF bytes")

        rc, out, _ = run_cli(b, "check")
        self.assertEqual(rc, 0, out)
        pins_of = lambda: re.findall(r"blob: ([0-9a-f]{40})", (b / "claims" / "c.md").read_text())
        before = pins_of()
        self.assertEqual(len(before), 1)
        self.assertEqual(run_cli(b, "verify", "c")[0], 0)
        # Compare pins only: until Task 4, verify still rewrites verified_at.
        self.assertEqual(pins_of(), before,
                         "re-verifying on the CRLF clone must not change the shared pin")


class RawOutsideGit(TmpCase):
    def test_a_line_ending_change_is_stale_outside_git(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.txt", "one\ntwo\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.txt",)))
        run_cli(root, "verify", "c")
        (root / "a.txt").write_bytes(b"one\r\ntwo\r\n")
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE", out)


class HasherModes(TmpCase):
    def test_a_raw_cache_entry_is_never_trusted_in_git_mode(self):
        p = write(self.tmp, "a.txt", "hello\n")
        os.utime(p, ns=(OLD, OLD))
        cache = self.tmp / "stat.json"
        raw = Hasher(self.tmp, cache, mode="raw")
        raw.blob("a.txt")
        raw.save()
        with mock.patch("claimlock.gitio.hash_paths", return_value={"a.txt": "f" * 40}) as hp:
            got = Hasher(self.tmp, cache, mode="git").blob("a.txt")
        self.assertEqual(got, "f" * 40)
        self.assertEqual(hp.call_count, 1)

    def test_prime_hashes_many_files_in_one_git_call(self):
        for n in "abc":
            write(self.tmp, f"{n}.txt", n)
        fake = {f"{n}.txt": n * 40 for n in "abc"}
        with mock.patch("claimlock.gitio.hash_paths", return_value=fake) as hp:
            h = Hasher(self.tmp, None, mode="git")
            h.prime(["a.txt", "b.txt", "c.txt", "missing.txt"])
            got = [h.blob(f"{n}.txt") for n in "abc"]
        self.assertEqual(got, ["a" * 40, "b" * 40, "c" * 40])
        self.assertEqual(hp.call_count, 1)
        self.assertEqual(hp.call_args[0][1], ["a.txt", "b.txt", "c.txt"])

    def test_a_primed_hash_is_discarded_when_the_file_changed_after_priming(self):
        write(self.tmp, "a.txt", "one")
        h = Hasher(self.tmp, None, mode="git")
        with mock.patch("claimlock.gitio.hash_paths", return_value={"a.txt": "1" * 40}):
            h.prime(["a.txt"])
        write(self.tmp, "a.txt", "one, and longer")
        with mock.patch("claimlock.gitio.hash_paths", return_value={"a.txt": "2" * 40}):
            self.assertEqual(h.blob("a.txt"), "2" * 40)

    def test_git_failure_falls_back_to_raw_bytes(self):
        write(self.tmp, "a.txt", "hello\n")
        with mock.patch("claimlock.gitio.hash_paths", return_value=None):
            self.assertEqual(Hasher(self.tmp, None, mode="git").blob("a.txt"), blob_of_bytes(b"hello\n"))

    def test_use_cache_false_ignores_a_matching_cache_entry(self):
        p = write(self.tmp, "a.txt", "hello\n")
        os.utime(p, ns=(OLD, OLD))
        st = p.stat()
        h = Hasher(self.tmp, None, mode="raw")
        h.cache["a.txt"] = [st.st_size, st.st_mtime_ns, "0" * 40, "raw"]
        self.assertEqual(h.blob("a.txt"), "0" * 40)
        self.assertEqual(h.blob("a.txt", use_cache=False), blob_of_bytes(b"hello\n"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -p "test_hashing.py" -v`
Expected: `LineEndingsAcrossClones` fails (B reports STALE and/or `verify` rewrites the pin); the `HasherModes` tests fail with `TypeError: Hasher.__init__() got an unexpected keyword argument 'mode'` or `AttributeError: … hash_paths`. `RawOutsideGit` should already pass (control).

- [ ] **Step 3: Implement**

`lib/claimlock/gitio.py` — add:
```python
def hash_paths(root, rels):
    """{rel: blob} for root-relative files, hashed the way git stores them
    (clean filters and text/eol/autocrlf normalization applied), in one
    `git hash-object --stdin-paths`. None when git fails or any path cannot
    be hashed: the batch is all-or-nothing.

    Absolute paths are passed on purpose — `--stdin-paths` resolves relative
    paths from the repository top level, not the working directory."""
    if not rels:
        return {}
    base = Path(root).resolve()
    names = [str(base / rel) for rel in rels]
    if any("\n" in n for n in names):
        return None
    try:
        r = subprocess.run(["git", "hash-object", "--stdin-paths"], cwd=root,
                           input=("\n".join(names) + "\n").encode(), capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.decode("ascii", "replace").split()
    if len(out) != len(rels):
        return None
    return dict(zip(rels, out))
```

`lib/claimlock/pins.py` — add `from . import gitio` and replace the `Hasher` class (keep `blob_of_bytes`, `RACY_NS` and `save` unchanged):
```python
class Hasher:
    """Blob hashes of root-relative files, reusing a (size, mtime_ns) stat cache.

    mode "git": the blob git would store (normalized), batched by `prime`.
    mode "raw": sha1 of the raw bytes. Cache entries carry their mode, and an
    entry is trusted only in the mode that wrote it (legacy 3-item entries are
    raw), so a clone that gains or loses git never reuses the other's hashes.
    """

    def __init__(self, root: Path, cache_path, mode="raw"):
        self.root = Path(root)
        self.cache_path = Path(cache_path) if cache_path else None
        self.mode = mode
        self.cache = {}
        self.dirty = False
        self.hashed = 0
        self._primed = {}
        if self.cache_path and self.cache_path.is_file():
            try:
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self.cache = loaded if isinstance(loaded, dict) else {}
            except (ValueError, OSError):
                self.cache = {}

    def _cached(self, rel, st):
        e = self.cache.get(rel)
        if not isinstance(e, list) or len(e) not in (3, 4):
            return None
        mode = e[3] if len(e) == 4 else "raw"
        if mode == self.mode and e[0] == st.st_size and e[1] == st.st_mtime_ns:
            return e[2]
        return None

    def prime(self, rels):
        """Hash every uncached regular file in `rels` with one git call (git mode only)."""
        if self.mode != "git":
            return
        todo, stats = [], {}
        for rel in dict.fromkeys(rels):
            try:
                st = (self.root / rel).stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or self._cached(rel, st) is not None:
                continue
            todo.append(rel)
            stats[rel] = st
        got = gitio.hash_paths(self.root, todo) if todo else {}
        for rel, digest in (got or {}).items():
            self._primed[rel] = (stats[rel].st_size, stats[rel].st_mtime_ns, digest)

    def _digest(self, rel, st, use_primed):
        if self.mode == "git":
            primed = self._primed.pop(rel, None) if use_primed else None
            if primed and primed[0] == st.st_size and primed[1] == st.st_mtime_ns:
                return primed[2]
            got = gitio.hash_paths(self.root, [rel])
            if got is not None:
                return got[rel]
        try:
            return blob_of_bytes((self.root / rel).read_bytes())
        except OSError:
            return None

    def blob(self, rel: str, use_cache=True):
        """The file's pin, or None if it does not exist, is not a regular file,
        or cannot be read. `use_cache=False` re-hashes from disk (verify/resolve)."""
        try:
            st = (self.root / rel).stat()
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        self.hashed += 1
        if use_cache:
            hit = self._cached(rel, st)
            if hit is not None:
                return hit
        digest = self._digest(rel, st, use_primed=use_cache)
        if digest is None:
            return None
        if time.time_ns() - st.st_mtime_ns >= RACY_NS:
            self.cache[rel] = [st.st_size, st.st_mtime_ns, digest, self.mode]
            self.dirty = True
        elif rel in self.cache:
            del self.cache[rel]
            self.dirty = True
        return digest
```
Update the module docstring: pins are git's normalized blob inside a work tree (so LF/CRLF clones agree), raw bytes outside git; both equal `git hash-object --no-filters` when no conversion applies.

`lib/claimlock/claims.py`:
```python
def open_hasher(project):
    mode = "git" if gitio.in_git(project.root) else "raw"
    return Hasher(project.root, project.state_dir / "cache" / "stat.json", mode=mode)
```
In `evaluate`, after computing the path list used for anchors, prime the hasher with the same paths before calling `freshness`:
```python
def evaluate(project, hasher):
    claims = load_claims(project)
    paths = [s.path for c in claims if c.status == "verified" and not c.parse_error
             for s in c.sources if safe_source(project.root, s.path) is not None]
    hasher.prime(paths)
    anchors = anchors_for(project, paths)
    return [Result(c, problems(c, project), *freshness(c, project, hasher, anchors))
            for c in claims]
```

`lib/claimlock/ops.py` — in `verify`, replace the `contents` loop and its use so that pins come from the hasher with the cache bypassed:
```python
    hasher = C.open_hasher(project)
    pinned = []
    for s in c.sources:
        blob = hasher.blob(s.path, use_cache=False)
        if blob is None:
            raise Refused(f"{cid}: source {s.path} does not exist or cannot be read — "
                          f"fix its sources (or the file's permissions), then verify")
        pinned.append((s.path, blob))
```
Then build `sources=[{"path": p, "blob": b} for p, b in pinned]` and `return pinned`. Remove the now-unused `blob_of_bytes` import. Do not call `hasher.save()` here (verify must not seed the cache).

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s tests -p "test_hashing.py" -v` — all pass. Then the full suite plain and with `-W error::ResourceWarning`, and `python3 bin/claimlock self-test`. Existing `tests/test_pins.py` tests use the default raw mode and must pass unchanged. Report the observed total (+6 tests).

- [ ] **Step 5: Watch each guard fail for its own reason**

(a) In `Hasher._cached` temporarily remove `mode == self.mode and`; `test_a_raw_cache_entry_is_never_trusted_in_git_mode` fails. Re-apply.
(b) In `gitio.hash_paths` temporarily pass `rels` instead of `names` as the stdin paths; `test_lf_and_crlf_clones_agree_on_every_pin` fails (the batch cannot open top-level-relative paths from a subdirectory — `src/limit.py` sits one level down). If it does not fail, say so in the report and add a store-in-a-subdirectory variant that does. Re-apply.
(c) In `Hasher._digest` temporarily drop the stat comparison on `primed`; `test_a_primed_hash_is_discarded_when_the_file_changed_after_priming` fails. Re-apply. Suite green.

- [ ] **Step 6: Commit**

```bash
git add lib/claimlock/gitio.py lib/claimlock/pins.py lib/claimlock/claims.py lib/claimlock/ops.py tests/test_hashing.py
git commit -m "feat: pins are git's normalized blob inside a work tree, so clones agree" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
