"""Marker-delimited regions: the part of a file between a `claimlock:begin
<name>` line and a `claimlock:end <name>` line, pinned instead of the whole
file (spec 2026-09-15 sections 2.1-2.2).

A line matches a marker when it contains `claimlock:begin` or
`claimlock:end`, then whitespace, then a name; the greedy name pattern
naturally refuses to match a name that is a strict prefix of a longer one
(`claimlock:begin r1-extra` never opens `r1`, because the match for `r1`
would have to stop one character short of what the regex actually consumes).
"""
import re

from .hashing import blob_of_bytes

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MARKER_RE = re.compile(r"claimlock:(begin|end)\s+([a-z0-9][a-z0-9-]*)")


class RegionError(Exception):
    """str(e) is the exact reason text from spec 2.1."""


def extract(data: bytes, name: str) -> str:
    """The text strictly between the `begin`/`end` markers for `name` (2.2):
    UTF-8 decoded, lines split at "\\n" only, a trailing "\\r" dropped from
    each. Raises RegionError, with the reason spec 2.1 names, when the region
    cannot be found unambiguously.

    The whole file is scanned, not just up to the first matching `end`: a
    second complete `begin`/`end` pair, or an orphan `end` after the region
    already closed, is a loud failure, not silently ignored content."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RegionError("not UTF-8, so regions cannot be read") from None
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    begin_idx = end_idx = None
    open_ = False
    for i, line in enumerate(lines):
        m = _MARKER_RE.search(line)
        if not m or m.group(2) != name:
            continue
        if m.group(1) == "begin":
            if begin_idx is not None:
                raise RegionError(f"region {name!r} begins more than once")
            begin_idx = i
            open_ = True
        else:
            if begin_idx is None:
                raise RegionError(f"region {name!r} ends before it begins")
            if not open_:
                raise RegionError(f"region {name!r} ends more than once")
            end_idx = i
            open_ = False
    if begin_idx is None:
        raise RegionError(f"region {name!r} not found")
    if end_idx is None:
        raise RegionError(f"region {name!r} has no end marker")
    return "".join(line + "\n" for line in lines[begin_idx + 1:end_idx])


def region_hash(text: str) -> str:
    """The same blob-hash function as whole-file pins, applied to region
    text: both live in `hashing.blob_of_bytes`, imported here rather than
    from `pins` (which imports this module at module scope, so `regions`
    importing `pins` back would be circular)."""
    return blob_of_bytes(text.encode("utf-8"))
