"""Every git subprocess claimlock runs.

Each function returns None / False / [] when git is not installed, the
directory is not a repository, or the command fails. Git is an enhancement —
it serves prior content for `diff` and reveals commits — never a requirement.
"""
import os
import subprocess
from pathlib import Path


def run(root, *args):
    try:
        return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root,
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def _text(r):
    if r is None or r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").strip()


def in_git(root) -> bool:
    return _text(run(root, "rev-parse", "--is-inside-work-tree")) == "true"


def root_is_ignored(root) -> bool:
    """True when `root` itself lies inside a directory an enclosing repository
    ignores. `git ls-files` then lists nothing under it, so an empty listing
    there means "git will not tell us", not "there are no files"."""
    r = run(root, "check-ignore", "-q", ".")
    return r is not None and r.returncode == 0


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


def head(root):
    return _text(run(root, "rev-parse", "--verify", "-q", "HEAD")) or None


def has_blob(root, sha) -> bool:
    r = run(root, "cat-file", "-e", f"{sha}^{{blob}}")
    return r is not None and r.returncode == 0


def cat_blob(root, sha):
    r = run(root, "cat-file", "blob", sha)
    return r.stdout if r is not None and r.returncode == 0 else None


def _lines(out):
    return [l for l in (out or "").splitlines() if l]


def ls_files(root):
    """Tracked and untracked-but-not-ignored files under `root`, relative to it,
    or None when `root` is not in a git work tree or git fails.

    Deduplicated: during a merge conflict `--cached` lists a path once per stage.
    """
    r = run(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if r is None or r.returncode != 0:
        return None
    return sorted({os.fsdecode(p) for p in r.stdout.split(b"\0") if p})


def changed_paths(root, old, new) -> list:
    """Paths changed between two commits, relative to `root`, limited to it.

    If `old` is None or unreachable, the paths changed by `new` itself.
    """
    if old:
        out = _text(run(root, "diff", "--name-only", "--relative", "--no-renames", old, new))
        if out is not None:
            return _lines(out)
    out = _text(run(root, "diff-tree", "--no-commit-id", "--name-only", "--relative",
                    "--no-renames", "-r", "--root", new))
    return _lines(out)


def head_mark_paths(root) -> list:
    """Absolute paths whose mtime changes whenever HEAD moves (for a cheap stat gate)."""
    names = ["HEAD", "logs/HEAD", "packed-refs"]
    ref = _text(run(root, "symbolic-ref", "-q", "HEAD"))
    if ref:
        names += [ref, f"logs/{ref}"]
    args = []
    for n in names:
        args += ["--git-path", n]
    out = _text(run(root, "rev-parse", *args))
    return [str((Path(root) / p).resolve()) for p in _lines(out)]
