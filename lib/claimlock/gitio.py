"""Every git subprocess claimlock runs.

Each function returns None / False / [] when git is not installed, the
directory is not a repository, or the command fails. Git is an enhancement —
it serves prior content for `diff` and reveals commits — never a requirement.
"""
import os
import re
import subprocess
from pathlib import Path

_BLOB_RE = re.compile(r"^[0-9a-f]{40}$")


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
    blob that appeared at one of them in any commit reachable from any ref
    (full history — a merge that matches one parent at the path otherwise
    prunes the other side, even though its blobs stay reachable), plus the
    blob at every index stage (a conflicted merge; `ls-files -s` lists one
    line per stage) currently staged for each. None outside git or on
    failure. `--literal-pathspecs` on both commands: a source path containing
    glob metacharacters (`src/[id].ts`) must never match an unrelated file.

    `cat-file -e` is not used: it also reports loose objects that no commit
    references (written by `hash-object -w`, or staged then unstaged)."""
    if not rels:
        return set()
    prefix = _prefix(root)
    if prefix is None:
        return None
    r = run(root, "--literal-pathspecs", "rev-list", "--objects", "--all",
           "--full-history", "--", *rels)
    if r is None or r.returncode != 0:
        return None
    wanted = {prefix + rel for rel in rels}
    blobs = set()
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        sha, _, path = line.partition(" ")
        if path in wanted:
            blobs.add(sha)
    s = run(root, "--literal-pathspecs", "ls-files", "-s", "-z", "--", *rels)
    if s is not None and s.returncode == 0:
        for rec in s.stdout.split(b"\0"):
            meta, tab, _ = rec.partition(b"\t")
            parts = meta.split()
            if tab and len(parts) >= 2:
                blobs.add(parts[1].decode("ascii", "replace"))
    return blobs


def ignored_paths(root, rels):
    """Root-relative paths among `rels` that git ignores, in one batched
    `check-ignore --stdin`. Content at an ignored path can never be committed
    or staged, so it is exempt from anchoring rather than permanently unable
    to satisfy it. `check-ignore` exits 1 when none of the given paths are
    ignored — that is not a failure, only anything else is. None on failure.

    No `--literal-pathspecs` here (unlike `anchored_blobs`): `--stdin` paths
    are already matched literally with no pathspec-magic parsing — git 2.55
    refuses to even start (`pathspec magic not supported by this command:
    'literal'`) if the global flag is added on top of `--stdin`. Verified
    directly: querying `src/[id].ts` via `--stdin` does not match a tracked
    `src/i.ts` ignore rule, with or without the flag."""
    if not rels:
        return set()
    try:
        payload = b"\0".join(os.fsencode(rel) for rel in rels) + b"\0"
        r = subprocess.run(["git", "-c", "core.quotepath=off", "check-ignore", "--stdin", "-z"],
                           cwd=root, input=payload,
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None
    if r.returncode not in (0, 1):
        return None
    return {os.fsdecode(p) for p in r.stdout.split(b"\0") if p}


def hash_paths(root, rels):
    """{rel: blob} for root-relative files, hashed the way git stores them
    (clean filters and text/eol/autocrlf normalization applied), in one
    `git hash-object --stdin-paths`. None when git fails or any path cannot
    be hashed, including a name this function cannot even encode to send to
    git: the batch is all-or-nothing.

    Absolute paths are passed on purpose — `--stdin-paths` resolves relative
    paths from the repository top level, not the working directory. Names are
    encoded with `os.fsencode` (surrogateescape round-trips a non-UTF-8 name
    back to its exact original bytes) rather than `str.encode` (strict UTF-8,
    which raises `UnicodeEncodeError` on the lone surrogates `os.fsdecode`
    produces for such a name — every git subprocess here must degrade to
    None, never raise, on failure)."""
    if not rels:
        return {}
    base = Path(root).resolve()
    names = [str(base / rel) for rel in rels]
    if any("\n" in n for n in names):
        return None
    try:
        payload = b"\n".join(os.fsencode(n) for n in names) + b"\n"
        r = subprocess.run(["git", "hash-object", "--stdin-paths"], cwd=root,
                           input=payload, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None
    if r.returncode != 0:
        return None
    try:
        out = r.stdout.decode("ascii", "replace").split()
    except UnicodeError:
        return None
    if len(out) != len(rels):
        return None
    return dict(zip(rels, out))


def head(root):
    return _text(run(root, "rev-parse", "--verify", "-q", "HEAD")) or None


def user_email(root):
    return _text(run(root, "config", "user.email")) or None


def short_head(root):
    return _text(run(root, "rev-parse", "--short=7", "--verify", "-q", "HEAD")) or None


def merge_base(root, base):
    return _text(run(root, "merge-base", base, "HEAD")) or None


def changed_since(root, commit):
    """Paths changed by commits in commit..HEAD, relative to (and limited to)
    `root`: [] for a genuinely empty range, None when git fails. The gate must
    tell those apart — an empty scope passes, so a failure read as [] would
    pass a change nobody looked at."""
    out = _text(run(root, "diff", "--name-only", "--relative", "--no-renames", commit, "HEAD"))
    return _lines(out) if out is not None else None


def verifier(root, claim_rel, blob):
    """(author email, ISO time, short sha) of the latest commit that added or
    removed the exact pin line `    blob: <sha>` in the claim file (as
    `frontmatter._sources_block` writes it — four spaces, nothing else on the
    line), or None if no commit did.

    `--follow` so a rename of the claim file itself does not stop history
    from being searched past it (valid here because `claim_rel` is the only
    pathspec, which is what `--follow` requires). `-G` with a `^...$`-anchored
    pattern, not `-S` with a plain substring: `-S` matches any commit whose
    total occurrence COUNT of the string changed anywhere in the file, so a
    claim body merely mentioning "blob: <sha>" in prose (e.g. "(See blob:
    <sha> for details.)") changes that count and wrongly attributes the pin to
    whoever wrote the sentence. `-G` instead matches a commit whose diff added
    or removed a line matching the regex, and the anchors mean only the pin
    line itself — never a substring inside a longer line — can match."""
    pattern = f"^    blob: {re.escape(blob)}$"
    out = _text(run(root, "log", "--follow", "-1", "--format=%ae%x09%aI%x09%h",
                    "-G", pattern, "--", claim_rel))
    if not out:
        return None
    parts = out.split("\t")
    return tuple(parts) if len(parts) == 3 else None


def commits_behind(root, commit):
    out = _text(run(root, "rev-list", "--count", f"{commit}..HEAD"))
    return int(out) if out and out.isdigit() else None


def has_blob(root, sha) -> bool:
    r = run(root, "cat-file", "-e", f"{sha}^{{blob}}")
    return r is not None and r.returncode == 0


def cat_blob(root, sha):
    """The blob's content, or None. `sha` must be a full 40-hex object id: git
    revision syntax (`HEAD:secret.txt`, `HEAD~1:x`, …) is otherwise accepted
    by `cat-file`, which would let a malformed pin print an unrelated file."""
    if not _BLOB_RE.match(sha or ""):
        return None
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


def range_log(root, old, new):
    """[(short sha, author email, subject, [root-relative paths])], newest first,
    for commits in old..new — or just `new` when `old` is None or unreachable."""
    fmt = "--format=%x00%h%x09%aE%x09%s"  # %aE: the mailmap-canonical email
    r = run(root, "log", "--no-renames", "--name-only", "--relative", fmt, f"{old}..{new}") if old else None
    if r is None or r.returncode != 0:
        r = run(root, "log", "-1", "--no-renames", "--name-only", "--relative", fmt, new)
    if r is None or r.returncode != 0:
        return []
    out = []
    for chunk in r.stdout.decode("utf-8", "replace").split("\0")[1:]:
        lines = [line for line in chunk.splitlines() if line]
        if not lines:
            continue
        head = lines[0].split("\t", 2)
        if len(head) == 3:
            out.append((head[0], head[1], head[2], lines[1:]))
    return out


def operation_in_progress(root) -> bool:
    """True while a merge, rebase, cherry-pick, revert or `git am` is stopped
    mid-way: its working-tree changes are git's, not the person's own edits.
    `rebase-apply` / `rebase-merge` are directories, present for the whole
    of an in-progress `am` or rebase."""
    args = []
    for name in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                 "rebase-apply", "rebase-merge"):
        args += ["--git-path", name]
    out = _text(run(root, "rev-parse", *args))
    if out is None:
        return False
    return any((Path(root) / p).exists() for p in _lines(out))


def dirty_paths(root):
    """Tracked paths whose working-tree content differs from HEAD, root-relative."""
    out = _text(run(root, "diff", "--name-only", "--relative", "HEAD"))
    return _lines(out) if out is not None else []


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
