"""A pin is anchored when every clone can recover its content from git:
it appears at that path in a reachable commit, or it is staged right now."""
import json
import shutil
import subprocess
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

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
