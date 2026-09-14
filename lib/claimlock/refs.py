"""Claim markers in prose — ``Claim: `id` `` written beside the sentence it pins.

A marker naming no claim reads as pinned and pins nothing, which is worse than
no marker: nobody goes back to check a sentence that looks covered.

Globs use fnmatch semantics (`*` crosses directories); a leading `**/` also
matches at the root. Hidden directories, node_modules and the claims
directory are never scanned.
"""
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from .project import is_within, safe_source

SKIP_DIRS = {"node_modules"}


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


def _files(project, only):
    if only is not None:
        for rel in sorted(only):
            p = safe_source(project.root, rel)
            if (p is not None and p.is_file() and _matches(rel, project.marker_globs)
                    and not _excluded(project, rel)):
                yield p, rel
        return
    for dirpath, dirnames, filenames in os.walk(project.root):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames
                             if not n.startswith(".") and n not in SKIP_DIRS
                             and not is_within((d / n).resolve(), project.claims_dir))
        for f in sorted(filenames):
            rel = (d / f).relative_to(project.root).as_posix()
            if _matches(rel, project.marker_globs):
                yield d / f, rel


def scan(project, only=None):
    """(markers, files_scanned). `only` limits the scan to those root-relative paths."""
    markers, scanned = [], 0
    for p, rel in _files(project, only):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for n, line in enumerate(text.splitlines(), 1):
            for m in project.marker_pattern.finditer(line):
                markers.append(Marker(rel, n, m.group(1)))
    return markers, scanned
