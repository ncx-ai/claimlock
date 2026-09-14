### Task 3: Project discovery, config, safe paths, blob hashing

**Files:**
- Create: `lib/claimlock/project.py`, `lib/claimlock/pins.py`, `tests/test_project.py`, `tests/test_pins.py`

**Interfaces:**
- Consumes: `tests/helpers.py` (Task 2).
- Produces: `project.CONFIG`, `ConfigError`, `Project` (fields `root, claims_dir, marker_globs, marker_pattern, has_config`; property `state_dir`; method `has_store()`), `load(start)`, `safe_source(root, rel)`, `is_within(p, root)`; `pins.blob_of_bytes(data)`, `pins.Hasher(root, cache_path)` with `.blob(rel)`, `.hashed`, `.save()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_project.py`:
```python
import os
import shutil
import unittest

from helpers import TmpCase, git, make_repo, write
from claimlock.project import CONFIG, ConfigError, load, safe_source


class Root(TmpCase):
    def test_config_in_ancestor_wins(self):
        root = make_repo(self.tmp / "r", use_git=False)
        sub = root / "a" / "b"
        sub.mkdir(parents=True)
        p = load(sub)
        self.assertEqual(p.root, root)
        self.assertTrue(p.has_config)
        self.assertEqual(p.claims_dir, root / "claims")
        self.assertEqual(p.state_dir, root / ".claimlock")

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_git_toplevel_when_no_config(self):
        top = self.tmp / "g"
        top.mkdir()
        git(top, "init", "-q")
        sub = top / "x"
        sub.mkdir()
        p = load(sub)
        self.assertEqual(p.root, top)
        self.assertFalse(p.has_config)

    def test_start_dir_when_neither(self):
        d = self.tmp / "plain"
        d.mkdir()
        p = load(d)
        self.assertEqual(p.root, d)
        self.assertFalse(p.has_store())
        (d / "claims").mkdir()
        self.assertTrue(load(d).has_store())

    def test_config_values(self):
        root = make_repo(self.tmp / "r", False,
                         config='claims_dir = "docs/claims"\nmarker_globs = ["*.txt"]\n'
                                'marker_pattern = "See ([a-z-]+)"\n')
        p = load(root)
        self.assertEqual(p.claims_dir, root / "docs" / "claims")
        self.assertEqual(p.marker_globs, ["*.txt"])
        self.assertEqual(p.marker_pattern.pattern, "See ([a-z-]+)")

    def test_config_errors(self):
        cases = [
            ("unknown", 'claim_dir = "x"\n', "unknown key"),
            ("toml", "claims_dir = \n", CONFIG),
            ("type", "claims_dir = 3\n", "claims_dir must be a string"),
            ("globs", 'marker_globs = "*.md"\n', "marker_globs must be a list"),
            ("nogroup", 'marker_pattern = "Claim"\n', "capture group"),
            ("badre", 'marker_pattern = "("\n', "marker_pattern"),
            ("escape", 'claims_dir = "../elsewhere"\n', "escapes"),
        ]
        for name, cfg, needle in cases:
            with self.subTest(name):
                root = make_repo(self.tmp / name, False, config=cfg)
                with self.assertRaises(ConfigError) as cm:
                    load(root)
                self.assertIn(needle, str(cm.exception))


class SafeSource(TmpCase):
    def test_accepts_and_rejects(self):
        root = self.tmp / "r"
        root.mkdir()
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, root / "link")
        self.assertEqual(safe_source(root, "src/a.py"), root / "src" / "a.py")
        for bad in ["", "/etc/passwd", "../x", "a/../../x", "a\\b", "C:/x", "link/secret"]:
            with self.subTest(bad):
                self.assertIsNone(safe_source(root, bad))


if __name__ == "__main__":
    unittest.main()
```

`tests/test_pins.py`:
```python
import json
import os
import shutil
import subprocess
import time
import unittest

from helpers import TmpCase, write
from claimlock import pins
from claimlock.pins import Hasher, blob_of_bytes

OLD = time.time_ns() - 100 * 1_000_000_000  # 100 s ago: old enough to cache


class Blob(TmpCase):
    def test_known_git_values(self):
        self.assertEqual(blob_of_bytes(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
        self.assertEqual(blob_of_bytes(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_matches_git_hash_object_no_filters(self):
        p = write(self.tmp, "crlf.txt", "")
        p.write_bytes(b"line one\r\nline two\r\n\x00binary")
        out = subprocess.run(["git", "hash-object", "--no-filters", str(p)],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(blob_of_bytes(p.read_bytes()), out)


class HasherCase(TmpCase):
    def cache(self):
        return self.tmp / ".claimlock" / "cache" / "stat.json"

    def test_missing_and_directory_are_none(self):
        (self.tmp / "d").mkdir()
        h = Hasher(self.tmp, None)
        self.assertIsNone(h.blob("nope.txt"))
        self.assertIsNone(h.blob("d"))

    def test_hashes_and_counts(self):
        write(self.tmp, "a.txt", "hello\n")
        h = Hasher(self.tmp, None)
        self.assertEqual(h.blob("a.txt"), "ce013625030ba8dba906f756967f9e9ca394464a")
        self.assertEqual(h.hashed, 1)

    def test_old_entry_is_cached_and_reused_when_stat_matches(self):
        p = write(self.tmp, "a.txt", "hello\n")
        os.utime(p, ns=(OLD, OLD))
        h = Hasher(self.tmp, self.cache())
        first = h.blob("a.txt")
        h.save()
        self.assertTrue(self.cache().is_file())
        # Same size, same mtime, different bytes: the cache is trusted (documented trade-off).
        p.write_text("HELLO\n")
        os.utime(p, ns=(OLD, OLD))
        self.assertEqual(Hasher(self.tmp, self.cache()).blob("a.txt"), first)
        # Any mtime change invalidates the entry.
        os.utime(p, ns=(OLD + 1, OLD + 1))
        self.assertEqual(Hasher(self.tmp, self.cache()).blob("a.txt"), blob_of_bytes(b"HELLO\n"))

    def test_recent_mtime_is_never_cached(self):
        write(self.tmp, "a.txt", "hello\n")
        h = Hasher(self.tmp, self.cache())
        h.blob("a.txt")
        h.save()
        data = json.loads(self.cache().read_text()) if self.cache().exists() else {}
        self.assertNotIn("a.txt", data)

    def test_corrupt_cache_is_ignored(self):
        write(self.tmp, "a.txt", "hello\n")
        self.cache().parent.mkdir(parents=True)
        self.cache().write_text("{not json")
        self.assertEqual(Hasher(self.tmp, self.cache()).blob("a.txt"), blob_of_bytes(b"hello\n"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `ModuleNotFoundError: No module named 'claimlock.project'` and `... 'claimlock.pins'`.

- [ ] **Step 3: Implement `lib/claimlock/project.py`**

```python
"""Where a store lives and how it is configured.

The root is anchored by `.claimlock.toml` first, so a store created before a
repository exists does not move when `git init` runs later. Git is only a
fallback for finding the root, never a requirement.
"""
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG = ".claimlock.toml"
DEFAULTS = {
    "claims_dir": "claims",
    "marker_globs": ["**/*.md"],
    "marker_pattern": r"Claim: `([a-z0-9][a-z0-9-]*)`",
}


class ConfigError(Exception):
    pass


@dataclass
class Project:
    root: Path
    claims_dir: Path
    marker_globs: list
    marker_pattern: re.Pattern
    has_config: bool

    @property
    def state_dir(self) -> Path:
        return self.root / ".claimlock"

    def has_store(self) -> bool:
        return self.has_config or self.claims_dir.is_dir()


def is_within(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def _git_toplevel(start: Path):
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return Path(out).resolve() if r.returncode == 0 and out else None


def _find_root(start: Path):
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / CONFIG).is_file():
            return d, True
    return (_git_toplevel(start) or start), False


def load(start: Path) -> Project:
    root, has_config = _find_root(Path(start))
    cfg = dict(DEFAULTS)
    if has_config:
        try:
            data = tomllib.loads((root / CONFIG).read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ConfigError(f"{CONFIG}: {e}") from None
        unknown = sorted(set(data) - set(DEFAULTS))
        if unknown:
            raise ConfigError(f"{CONFIG}: unknown key(s) {unknown}; allowed: {sorted(DEFAULTS)}")
        cfg.update(data)
    if not isinstance(cfg["claims_dir"], str) or not cfg["claims_dir"]:
        raise ConfigError(f"{CONFIG}: claims_dir must be a string")
    globs = cfg["marker_globs"]
    if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
        raise ConfigError(f"{CONFIG}: marker_globs must be a list of strings")
    try:
        pattern = re.compile(cfg["marker_pattern"])
    except (re.error, TypeError) as e:
        raise ConfigError(f"{CONFIG}: marker_pattern: {e}") from None
    if pattern.groups < 1:
        raise ConfigError(f"{CONFIG}: marker_pattern needs a capture group for the claim id")
    claims_dir = (root / cfg["claims_dir"]).resolve()
    if not is_within(claims_dir, root):
        raise ConfigError(f"{CONFIG}: claims_dir escapes the project root")
    return Project(root, claims_dir, list(globs), pattern, has_config)


def safe_source(root: Path, rel: str):
    """The file a root-relative POSIX path names, or None if it could escape the root."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return None
    if re.match(r"^[A-Za-z]:", rel) or ".." in rel.split("/"):
        return None
    p = root / rel
    if not is_within(p.resolve(), root.resolve()):
        return None
    return p
```

- [ ] **Step 4: Implement `lib/claimlock/pins.py`**

```python
"""Content pins: the git blob SHA of a file, computed without git.

`sha1(b"blob <len>\\0" + bytes)` is exactly `git hash-object --no-filters`, so
a pin written in a plain directory is still valid after `git init`, and git's
object store can serve the pinned content back for `diff`.
"""
import hashlib
import json
import os
import stat
import time
from pathlib import Path

# An entry whose mtime is this recent is not cached: a same-size edit inside
# the filesystem's timestamp granularity would otherwise be invisible. Git's
# "racy git" guard is the same idea.
RACY_NS = 2_000_000_000


def blob_of_bytes(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


class Hasher:
    """Blob hashes of root-relative files, reusing a (size, mtime_ns) stat cache."""

    def __init__(self, root: Path, cache_path):
        self.root = Path(root)
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = {}
        self.dirty = False
        self.hashed = 0
        if self.cache_path and self.cache_path.is_file():
            try:
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self.cache = loaded if isinstance(loaded, dict) else {}
            except (ValueError, OSError):
                self.cache = {}

    def blob(self, rel: str):
        """The file's blob SHA, or None if it does not exist or is not a regular file."""
        try:
            st = (self.root / rel).stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        self.hashed += 1
        entry = self.cache.get(rel)
        if (isinstance(entry, list) and len(entry) == 3
                and entry[0] == st.st_size and entry[1] == st.st_mtime_ns):
            return entry[2]
        digest = blob_of_bytes((self.root / rel).read_bytes())
        if time.time_ns() - st.st_mtime_ns >= RACY_NS:
            self.cache[rel] = [st.st_size, st.st_mtime_ns, digest]
            self.dirty = True
        elif rel in self.cache:
            del self.cache[rel]
            self.dirty = True
        return digest

    def save(self) -> None:
        if not (self.dirty and self.cache_path):
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError:
            pass  # a cache that cannot be written only costs speed
```

- [ ] **Step 5: Run to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK`; the run includes `test_project` (6 tests) and `test_pins` (7 tests) plus the 9 from Task 2 — `Ran 22 tests` (skips allowed only where git is absent; report the skip count).

- [ ] **Step 6: Watch the racy-mtime guard fail for its own reason**

Temporarily change `if time.time_ns() - st.st_mtime_ns >= RACY_NS:` to `if True:`; run; expected: `test_recent_mtime_is_never_cached` FAILS (`'a.txt' unexpectedly found`). Re-apply the inverse edit; rerun; OK.

- [ ] **Step 7: Commit**

```bash
git add lib/claimlock/project.py lib/claimlock/pins.py tests/test_project.py tests/test_pins.py
git commit -m "feat: project discovery, config, and git-compatible content pins" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
