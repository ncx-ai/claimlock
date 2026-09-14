"""Content pins: the git blob SHA of a file, computed without git.

`sha1(b"blob <len>\\0" + bytes)` is exactly `git hash-object --no-filters`, so
a pin written in a plain directory is still valid after `git init`, and git's
object store can serve the pinned content back for `diff`.
"""
import hashlib
import json
import os
import stat
import tempfile
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
        """The file's blob SHA, or None if it does not exist, is not a regular
        file, or cannot be read. One unreadable source is reported as that
        claim's `missing` state, never raised: an exception here would silence
        every other claim in the store (and every hook)."""
        try:
            st = (self.root / rel).stat()
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        self.hashed += 1
        entry = self.cache.get(rel)
        if (isinstance(entry, list) and len(entry) == 3
                and entry[0] == st.st_size and entry[1] == st.st_mtime_ns):
            return entry[2]
        try:
            digest = blob_of_bytes((self.root / rel).read_bytes())
        except OSError:
            return None
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
            fd, tmp = tempfile.mkstemp(prefix=self.cache_path.name + ".", suffix=".tmp",
                                       dir=str(self.cache_path.parent))
        except OSError:
            return  # a cache that cannot be written only costs speed
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.cache))
            os.replace(tmp, self.cache_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
