"""Two clones pin the same claim to different content; the merge conflicts on
the claim's pins; `claimlock resolve` keeps only a pin matching the merged
source, else hands the claim off as owed by the merger."""
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import TmpCase, claim_text, git, run_cli, write
from claimlock.merge import hunks

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


class Hunks(unittest.TestCase):
    def test_two_way_and_diff3(self):
        lines = ["a", "<<<<<<< HEAD", "ours", "||||||| base", "old", "=======", "theirs", ">>>>>>> x", "b"]
        [h] = hunks(lines)
        self.assertEqual((h.start, h.end, h.ours, h.theirs), (1, 7, ["ours"], ["theirs"]))

    def test_unterminated_is_an_error(self):
        with self.assertRaises(ValueError):
            hunks(["<<<<<<< HEAD", "x", "======="])


def _clone(bare, dest, email):
    subprocess.run(["git", "clone", "-q", str(bare), str(dest)], check=True, capture_output=True)
    for k, v in (("user.email", email), ("user.name", email.split("@")[0]),
                 ("commit.gpgsign", "false"), ("pull.rebase", "false")):
        git(dest, "config", k, v)


@NEED_GIT
class ResolveAfterMerge(TmpCase):
    def setUp(self):
        super().setUp()
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True, capture_output=True)
        self.a, self.b = self.tmp / "a", self.tmp / "b"
        _clone(bare, self.a, "amy@example.com")
        run_cli(self.a, "init")
        write(self.a, "src.py", "MAX = 1\n")
        write(self.a, "claims/c.md", claim_text("c", sources=("src.py",)))
        run_cli(self.a, "verify", "c")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "c")
        git(self.a, "push", "-q", "origin", "main")
        _clone(bare, self.b, "ben@example.com")

    def both_verify(self, a_content, b_content):
        write(self.a, "src.py", a_content)
        run_cli(self.a, "verify", "c")
        git(self.a, "commit", "-qam", "a")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "src.py", b_content)
        run_cli(self.b, "verify", "c")
        git(self.b, "commit", "-qam", "b")
        return subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.b, capture_output=True, text=True)

    def test_identical_verifications_merge_cleanly(self):
        pull = self.both_verify("MAX = 2\n", "MAX = 2\n")
        self.assertEqual(pull.returncode, 0, pull.stderr)
        self.assertEqual(run_cli(self.b, "check")[0], 0)

    def test_theirs_content_keeps_theirs_pin(self):
        self.assertNotEqual(self.both_verify("MAX = 2\n", "MAX = 3\n").returncode, 0)
        git(self.b, "checkout", "--theirs", "src.py")
        rc, out, err = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("KEPT", out)
        git(self.b, "add", "-A")
        rc, out, _ = run_cli(self.b, "check")
        self.assertEqual(rc, 0, out)

    def test_ours_content_keeps_ours_pin(self):
        self.both_verify("MAX = 2\n", "MAX = 3\n")
        git(self.b, "checkout", "--ours", "src.py")
        rc, out, _ = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0)
        self.assertIn("KEPT", out)
        git(self.b, "add", "-A")
        self.assertEqual(run_cli(self.b, "check")[0], 0)

    def test_new_content_makes_the_claim_owed_by_the_merger(self):
        self.both_verify("MAX = 2\n", "MAX = 3\n")
        write(self.b, "src.py", "MAX = 4\n")
        rc, out, _ = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0)
        self.assertIn("OWED", out)
        text = (self.b / "claims" / "c.md").read_text()
        self.assertIn("status: owed", text)
        self.assertIn("ben@example.com", text)
        self.assertNotIn("<<<<<<<", text)

    def test_a_prose_conflict_is_left_for_a_person(self):
        write(self.a, "claims/c.md", (self.a / "claims" / "c.md").read_text().replace("The thing holds.", "Amy's wording."))
        git(self.a, "commit", "-qam", "a prose")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "claims/c.md", (self.b / "claims" / "c.md").read_text().replace("The thing holds.", "Ben's wording."))
        git(self.b, "commit", "-qam", "b prose")
        subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.b, capture_output=True)
        rc, out, _ = run_cli(self.b, "resolve")
        self.assertEqual(rc, 1)
        self.assertIn("LEFT", out)
        self.assertIn("<<<<<<<", (self.b / "claims" / "c.md").read_text())


if __name__ == "__main__":
    unittest.main()
