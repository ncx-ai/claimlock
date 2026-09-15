"""Content pins: the git blob a file's content would hash to.

Inside a git work tree, a pin is git's normalized blob (`git hash-object
--stdin-paths`, so clean filters and `text`/`eol`/`core.autocrlf` normalization
apply) — an LF checkout and a CRLF checkout of the same commit agree. Outside
git, a pin is `sha1(b"blob <len>\\0" + bytes)`, exactly `git hash-object
--no-filters`, so a pin written in a plain directory is still valid after
`git init`. Both equal `git hash-object --no-filters` when no conversion
applies, and git's object store can serve either pinned content back for
`diff`.
"""
import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path

from . import gitio

# An entry whose mtime is this recent is not cached: a same-size edit inside
# the filesystem's timestamp granularity would otherwise be invisible. Git's
# "racy git" guard is the same idea.
RACY_NS = 2_000_000_000


# Environment that redirects where git reads config or attributes from, or
# supplies config itself. GIT_CONFIG_KEY_<n> / GIT_CONFIG_VALUE_<n> are matched
# by prefix.
_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CONFIG", "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
            "GIT_ATTR_NOSYSTEM", "GIT_ATTR_SOURCE", "HOME", "XDG_CONFIG_HOME")


def blob_of_bytes(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _file_sig(path) -> str:
    """A settings file's content digest: "-" when it does not exist, "?" when
    it cannot be read (git cannot read it either, so it applies to nothing)."""
    try:
        return blob_of_bytes(Path(path).read_bytes())
    except (FileNotFoundError, NotADirectoryError):
        return "-"
    except OSError:
        return "?"


def git_dirs(root):
    """(work tree top, git dir, common dir) of the repository holding `root`,
    found the way git finds it — the nearest `.git` directory, or a `.git`
    file naming one (a linked worktree or submodule) — without running git.
    None when there is none."""
    start = Path(root).resolve()
    for top in (start, *start.parents):
        dot = top / ".git"
        try:
            if dot.is_dir():
                gitdir = dot
            elif dot.is_file():
                line = dot.read_text(encoding="utf-8", errors="replace").strip()
                if not line.startswith("gitdir:"):
                    return None
                gitdir = (top / line[len("gitdir:"):].strip()).resolve()
            else:
                continue
            common = gitdir
            if (gitdir / "commondir").is_file():
                rel = (gitdir / "commondir").read_text(encoding="utf-8", errors="replace").strip()
                common = (gitdir / rel).resolve()
        except OSError:
            return None
        return top, gitdir, common
    return None


class Hasher:
    """Blob hashes of root-relative files, reusing a (size, mtime_ns) stat cache.

    mode "git": the blob git would store (normalized), batched by `prime`.
    mode "raw": sha1 of the raw bytes. Cache entries carry a tag (`_tag`), and
    an entry is trusted only under the tag that wrote it (legacy 3-item
    entries are raw), so a clone that gains or loses git never reuses the
    other's hashes, and in git mode a change to any setting that decides how
    git converts the file re-hashes it rather than trusting the old rules.
    """

    def __init__(self, root: Path, cache_path, mode="raw"):
        self.root = Path(root)
        self.cache_path = Path(cache_path) if cache_path else None
        self.mode = mode
        self.cache = {}
        self.dirty = False
        self.hashed = 0
        self._primed = {}
        self._settings = None
        self._top = None
        self._root_resolved = None
        self._attrs = {}
        self._tags = {}
        if self.cache_path and self.cache_path.is_file():
            try:
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self.cache = loaded if isinstance(loaded, dict) else {}
            except (ValueError, OSError):
                self.cache = {}

    def _git_settings(self):
        """Signature of the conversion settings that are the same for every
        file: config and attribute files at each level git reads them from by
        default, plus the environment that redirects or supplies them.
        Computed once per Hasher, from file contents — no git call, so a hook
        that runs no git keeps running none."""
        if self._settings is None:
            env = sorted((k, v) for k, v in os.environ.items()
                         if k in _GIT_ENV or k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")))
            home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
            xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
            files = [os.environ.get("GIT_CONFIG_GLOBAL") or home / ".gitconfig",
                     xdg / "git" / "config", xdg / "git" / "attributes",
                     os.environ.get("GIT_CONFIG_SYSTEM") or "/etc/gitconfig", "/etc/gitattributes"]
            dirs = git_dirs(self.root)
            if dirs:
                self._top, gitdir, common = dirs
                files += [common / "config", gitdir / "config.worktree", common / "info" / "attributes"]
            else:
                self._top = self.root.resolve()
            self._settings = json.dumps([env, [_file_sig(f) for f in files]])
        return self._settings

    def _tag(self, rel):
        """The tag a cache entry for `rel` must carry to be trusted: the mode,
        and in git mode a digest of the shared settings plus every
        `.gitattributes` from the work tree top down to the file's directory."""
        if self.mode != "git":
            return self.mode
        settings = self._git_settings()
        if self._root_resolved is None:
            self._root_resolved = self.root.resolve()
        parent = (self._root_resolved / rel).parent
        tag = self._tags.get(parent)  # the same for every file in a directory
        if tag is not None:
            return tag
        try:
            parts = parent.relative_to(self._top).parts
        except ValueError:
            parts = ()
        sigs, d = [], self._top
        for part in ("", *parts):
            d = d / part if part else d
            key = str(d)
            if key not in self._attrs:
                self._attrs[key] = _file_sig(d / ".gitattributes")
            sigs.append(self._attrs[key])
        tag = "git:" + hashlib.sha1("\n".join([settings, *sigs]).encode("ascii")).hexdigest()
        self._tags[parent] = tag
        return tag

    def _cached(self, rel, st):
        e = self.cache.get(rel)
        if not isinstance(e, list) or len(e) not in (3, 4):
            return None
        tag = e[3] if len(e) == 4 else "raw"
        if e[0] == st.st_size and e[1] == st.st_mtime_ns and tag == self._tag(rel):
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
        or cannot be read. One unreadable source is reported as that claim's
        `missing` state, never raised: an exception here would silence every
        other claim in the store (and every hook). `use_cache=False` re-hashes
        from disk (verify/resolve)."""
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
            self.cache[rel] = [st.st_size, st.st_mtime_ns, digest, self._tag(rel)]
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
