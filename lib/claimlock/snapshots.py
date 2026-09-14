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
