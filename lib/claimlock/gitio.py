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
