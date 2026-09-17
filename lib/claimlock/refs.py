"""Claim markers in prose — ``Claim: `id` `` written beside the sentence it pins.

A marker naming no claim reads as pinned and pins nothing, which is worse than
no marker: nobody goes back to check a sentence that looks covered.

Globs use fnmatch semantics (`*` crosses directories); a leading `**/` also
matches at the root. Hidden directories, node_modules and the claims
directory are never scanned. Inside a git work tree the candidates come from
`git ls-files --cached --others --exclude-standard`, so gitignored files are
not scanned either. The tree is walked instead outside git, when git fails,
and when the store root itself lies inside a directory an enclosing
repository ignores (git lists nothing under it there).

A marker inside a fenced code block (the marker syntax quoted as an example,
not written as prose) is never a citation: `scan` recognises ``` and ~~~
fences (CommonMark's rules, minus indented code blocks — see docs/format.md)
and counts what it skipped there separately, so a scanner bug that dropped
markers on the floor cannot pass silently as "nothing dangling".
"""
import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import gitio
from .project import is_within, safe_source

SKIP_DIRS = {"node_modules"}

# A fence-opening line: up to 3 leading spaces, then a run of 3+ backticks or
# tildes, then an optional info string (the rest of the line).
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _fence_close_re(char, length):
    # A closing fence: same character, at least as long, followed by nothing
    # but spaces/tabs. Any other trailing content means the line does NOT
    # close the fence (CommonMark) -- it is just more fenced content.
    return re.compile(r"^ {0,3}" + re.escape(char) + "{" + str(length) + r",}[ \t]*$")


def _fenced_mask(lines):
    """One bool per line in `lines`: True where the line is inside a fenced
    code block, the block's own delimiter lines included. An unclosed fence
    runs to end of file (CommonMark), which falls out of this being a single
    forward pass with no lookahead."""
    mask = [False] * len(lines)
    fence_char = fence_len = close_re = None
    for i, line in enumerate(lines):
        if fence_char is None:
            m = _FENCE_OPEN_RE.match(line)
            if not m:
                continue
            run, info = m.groups()
            ch = run[0]
            if ch == "`" and "`" in info:
                # A backtick-fence info string may not itself contain a
                # backtick (CommonMark) -- this line does not open a fence.
                continue
            fence_char, fence_len = ch, len(run)
            close_re = _fence_close_re(fence_char, fence_len)
            mask[i] = True
        else:
            mask[i] = True
            if close_re.match(line):
                fence_char = fence_len = close_re = None
    return mask


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


def files(project, only=None, globs=None):
    globs = project.marker_globs if globs is None else globs
    if only is not None:
        for rel in sorted(only):
            p = safe_source(project.root, rel)
            if (p is not None and p.is_file() and _matches(rel, globs)
                    and not _excluded(project, rel)):
                yield p, rel
        return
    # A store inside a directory an enclosing repo ignores gets an empty
    # listing from git; walk instead of silently scanning nothing.
    listed = None if gitio.root_is_ignored(project.root) else gitio.ls_files(project.root)
    if listed is not None:
        # Inside a git work tree: one `git ls-files` instead of walking every
        # directory, so ignored trees (target/, build/, vendor/…) cost nothing.
        for rel in listed:
            p = project.root / rel
            if _matches(rel, globs) and not _excluded(project, rel) and p.is_file():
                yield p, rel
        return
    for dirpath, dirnames, filenames in os.walk(project.root):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames
                             if not n.startswith(".") and n not in SKIP_DIRS
                             and not is_within((d / n).resolve(), project.claims_dir))
        for f in sorted(filenames):
            rel = (d / f).relative_to(project.root).as_posix()
            if _matches(rel, globs):
                yield d / f, rel


def scan(project, only=None):
    """(markers, files_scanned, skipped_in_fences). `only` limits the scan to
    those root-relative paths. A marker whose line falls inside a fenced code
    block is counted in `skipped_in_fences`, not in `markers` -- it names no
    citation, so it never makes something dangling or un-orphans a claim."""
    markers, scanned, skipped = [], 0, 0
    for p, rel in files(project, only):
        try:
            text = p.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        # Split at "\n" only, so a marker's line number is the one an editor
        # or `git grep -n` shows: `splitlines` also breaks at form feeds.
        lines = text.split("\n")
        fenced = _fenced_mask(lines)
        for n, line in enumerate(lines, 1):
            for m in project.marker_pattern.finditer(line):
                if fenced[n - 1]:
                    skipped += 1
                else:
                    markers.append(Marker(rel, n, m.group(1)))
    return markers, scanned, skipped
