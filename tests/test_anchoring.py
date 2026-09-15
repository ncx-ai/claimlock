"""A pin is anchored when every clone can recover its content from git:
it appears at that path in a reachable commit, or it is staged right now."""
import json
import os
import shutil
import subprocess
import unittest

from helpers import TmpCase, claim_text, git, make_repo, pinned_text, run_cli, write
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def state_of(root, cid="c"):
    rc, out, err = run_cli(root, "check", "--json")
    data = json.loads(out)
    return {r["id"]: r["state"] for r in data["results"]}[cid]


@NEED_GIT
class Anchoring(TmpCase):
    def store(self, root=None):
        root = root or make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        return root

    def test_verified_unstaged_is_unanchored_until_staged(self):
        root = self.store()
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual(state_of(root), "unanchored")
        git(root, "add", "a.py")
        self.assertEqual(state_of(root), "fresh")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        self.assertEqual(state_of(root), "fresh")

    def test_content_from_an_older_commit_stays_anchored(self):
        root = self.store()
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "one")
        write(root, "a.py", "two\n")
        git(root, "commit", "-qam", "two")
        write(root, "a.py", "one\n")  # back to the verified content, uncommitted
        self.assertEqual(state_of(root), "fresh")

    def test_a_blob_written_but_never_committed_is_unanchored(self):
        root = self.store()
        write(root, "a.py", "loose\n")
        subprocess.run(["git", "hash-object", "-w", "a.py"], cwd=root, check=True, capture_output=True)
        run_cli(root, "verify", "c")
        self.assertEqual(state_of(root), "unanchored")

    def test_a_staged_then_unstaged_blob_is_unanchored(self):
        root = self.store()
        write(root, "a.py", "staged\n")
        git(root, "add", "a.py")
        git(root, "reset", "-q", "a.py")
        run_cli(root, "verify", "c")
        self.assertEqual(state_of(root), "unanchored")

    def test_store_in_a_subdirectory_of_the_repository(self):
        # Control: the anchoring commit here is HEAD, so the independent
        # `ls-files -s` leg covers for a broken `prefix` computation — it's
        # `test_subdirectory_store_anchors_from_an_older_commit` below (older,
        # non-HEAD commit) that actually catches a prefix bug.
        outer = self.tmp / "outer"
        outer.mkdir()
        git(outer, "init", "-q", "-b", "main")
        git(outer, "config", "user.email", "t@example.com")
        git(outer, "config", "user.name", "t")
        git(outer, "config", "commit.gpgsign", "false")
        root = self.store(make_repo(outer / "proj", use_git=False))
        run_cli(root, "verify", "c")
        git(outer, "add", "-A")
        git(outer, "commit", "-qm", "sub")
        self.assertEqual(state_of(root), "fresh")

    def test_subdirectory_store_anchors_from_an_older_commit(self):
        outer = self.tmp / "outer"
        outer.mkdir()
        git(outer, "init", "-q", "-b", "main")
        git(outer, "config", "user.email", "t@example.com")
        git(outer, "config", "user.name", "t")
        git(outer, "config", "commit.gpgsign", "false")
        root = self.store(make_repo(outer / "proj", use_git=False))
        run_cli(root, "verify", "c")  # pins blob-of("one\n")
        git(outer, "add", "-A")
        git(outer, "commit", "-qm", "one")  # X is now only in this (non-HEAD-after-the-next-commit) commit
        write(root, "a.py", "two\n")
        git(outer, "commit", "-qam", "two")  # HEAD and the index both now hold Y ("two\n")
        write(root, "a.py", "one\n")  # back to X, uncommitted and unstaged
        self.assertEqual(state_of(root), "fresh")

    def test_content_survives_a_merge_that_matches_one_parent(self):
        # Finding 1: `rev-list --objects --all -- path` simplifies history —
        # a merge that is TREESAME to a parent at this path prunes the other
        # side's commits, even though their blobs stay reachable (`git
        # cat-file -t` still answers `blob`). `--full-history` disables that
        # simplification. main: a.py=X; branch f: Y then back to X; merge f
        # into main (TREESAME on a.py); delete f — Y is buried but reachable.
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "X\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "X")
        git(root, "checkout", "-q", "-b", "f")
        write(root, "a.py", "Y\n")
        git(root, "commit", "-qam", "Y")
        write(root, "a.py", "X\n")
        git(root, "commit", "-qam", "back-to-X")
        git(root, "checkout", "-q", "main")
        git(root, "merge", "--no-ff", "-q", "-m", "merge", "f")
        git(root, "branch", "-D", "f")
        y_blob = blob_of_bytes(b"Y\n")
        write(root, "claims/c.md", pinned_text("c", [("a.py", y_blob)]))
        write(root, "a.py", "Y\n")  # back to Y, uncommitted and unstaged
        self.assertEqual(state_of(root), "fresh")

    def test_malformed_blob_never_looked_up_as_a_revision(self):
        # Finding 2: `blob` is claim-authored text that used to reach
        # `git cat-file blob <sha>` verbatim; git accepts revision syntax
        # there (`HEAD:secret.txt`), so an unvalidated lookup could print an
        # unrelated committed file as a claim's "prior content".
        root = self.store()
        write(root, "secret.txt", "TOP SECRET\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "add secret")
        write(root, "claims/c.md", pinned_text("c", [("a.py", "HEAD:secret.txt")]))
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("malformed blob", out)
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertNotIn("TOP SECRET", out)
        self.assertIn("unavailable", out)

    def test_root_ignored_by_enclosing_repo_is_never_unanchored(self):
        # Finding 3 / T6 (a): a store inside a directory an enclosing
        # repository ignores can never be committed or staged at all —
        # anchoring must not be evaluated there.
        outer = self.tmp / "outer"
        outer.mkdir()
        git(outer, "init", "-q", "-b", "main")
        git(outer, "config", "user.email", "t@example.com")
        git(outer, "config", "user.name", "t")
        git(outer, "config", "commit.gpgsign", "false")
        write(outer, ".gitignore", "proj/\n")
        root = make_repo(outer / "proj", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        rc, _, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, err)
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 0, out)
        self.assertEqual(state_of(root), "fresh")

    def test_gitignored_source_is_exempt_from_anchoring(self):
        # Finding 3 / T6 (b): a normal (non-ignored) repo, but the SOURCE
        # itself is one `.gitignore` excludes — it can never be committed or
        # staged either, so it is exempt the same way.
        root = self.store()
        write(root, ".gitignore", ".claimlock/\ngenerated.py\n")
        write(root, "generated.py", "gen\n")
        write(root, "claims/g.md", claim_text("g", sources=("generated.py",)))
        rc, _, err = run_cli(root, "verify", "g")
        self.assertEqual(rc, 0, err)
        self.assertEqual(state_of(root, "g"), "fresh")

    def test_non_ignored_untracked_source_still_reads_unanchored(self):
        # Finding 3 (c), control: the exemption is specific to paths git
        # actually refuses to track — an ordinary untracked source must
        # still read unanchored.
        root = self.store()
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual(state_of(root), "unanchored")

    def test_pathspec_glob_does_not_anchor_an_unrelated_file(self):
        # Finding 4 / T7: sources are passed as pathspecs; without
        # `--literal-pathspecs` a bracket-glob path like `src/[id].ts` matches
        # an unrelated tracked file (`src/i.ts`) and anchors on ITS blob.
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "src/i.ts", "C\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "i")
        write(root, "src/[id].ts", "C\n")  # untracked; same content as src/i.ts
        write(root, "claims/c.md", claim_text("c", sources=("src/[id].ts",)))
        rc, _, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, err)
        self.assertEqual(state_of(root), "unanchored")

    def test_a_symlinked_source_anchors_at_its_target(self):
        # A pin hashes the link target's content; git stores the link text at
        # the link's path, so that content is anchored only at the target path.
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "real.py", "one\n")
        os.symlink("real.py", root / "link.py")
        write(root, "claims/c.md", claim_text("c", sources=("link.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        self.assertEqual(state_of(root), "fresh")
        # Control: the target anchors real content, it is not an exemption.
        write(root, "real.py", "two\n")  # uncommitted and unstaged
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual(state_of(root), "unanchored")

    def test_diff_explains_an_unanchored_pin(self):
        root = self.store()
        run_cli(root, "verify", "c")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0)
        self.assertIn("never committed or staged", out)

    def test_diff_reads_prior_content_from_git(self):
        root = self.store()
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        write(root, "a.py", "two\n")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("-one", out)
        self.assertIn("+two", out)
        self.assertIn("(verified)", out)


class OutsideGit(TmpCase):
    def test_never_unanchored_and_diff_says_unavailable(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        run_cli(root, "verify", "c")
        self.assertEqual(state_of(root), "fresh")
        self.assertFalse((root / ".claimlock" / "objects").exists())
        write(root, "a.py", "two\n")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("unavailable", out)


if __name__ == "__main__":
    unittest.main()
