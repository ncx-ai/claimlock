import os
import shutil
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")
NO_IDENTITY = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


class Owe(TmpCase):
    def store(self, use_git=False):
        root = make_repo(self.tmp / "r", use_git=use_git)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        if use_git:
            git(root, "add", "-A")
            git(root, "commit", "-qm", "c")
        return root

    def test_owe_a_stale_claim_to_someone(self):
        root = self.store()
        write(root, "a.py", "two\n")
        rc, out, err = run_cli(root, "owe", "c", "--to", "bob@example.com", "--reason", "MAX moved into config",
                               env=NO_IDENTITY)
        self.assertEqual(rc, 0, err)
        self.assertIn("owed c → bob@example.com (since none)", out)
        text = (root / "claims" / "c.md").read_text()
        self.assertIn("status: owed", text)
        self.assertIn("owed_since: none", text)
        self.assertRegex(text, r"\nOwed \d{4}-\d\d-\d\d by unknown: MAX moved into config\n$")
        self.assertEqual(run_cli(root, "check")[0], 0, "owed never fails check")

    def test_refusals(self):
        root = self.store()
        rc, _, err = run_cli(root, "owe", "c", "--to", "bob@example.com")
        self.assertEqual(rc, 1)
        self.assertIn("is fresh", err)
        write(root, "claims/u.md", claim_text("u", sources=("a.py",)))
        rc, _, err = run_cli(root, "owe", "u", "--to", "bob@example.com")
        self.assertEqual(rc, 1)
        self.assertIn("unverified", err)
        write(root, "a.py", "two\n")
        self.assertEqual(run_cli(root, "owe", "c", "--to", "bob@example.com")[0], 0)
        rc, _, err = run_cli(root, "owe", "c", "--to", "bob@example.com")
        self.assertEqual(rc, 1)
        self.assertIn("already owed", err)
        self.assertEqual(run_cli(root, "owe", "c", "--to", "amy@example.com")[0], 0)
        self.assertEqual(run_cli(root, "owe", "nope", "--to", "bob@example.com")[0], 1)

    def test_no_identity_outside_git_is_exit_2(self):
        root = self.store()
        write(root, "a.py", "two\n")
        rc, _, err = run_cli(root, "owe", "c", env=NO_IDENTITY)
        self.assertEqual(rc, 2)
        self.assertIn("--to", err)

    @NEED_GIT
    def test_inside_git_defaults_to_your_email_and_head(self):
        root = self.store(use_git=True)
        write(root, "a.py", "two\n")
        rc, out, err = run_cli(root, "owe", "c")
        self.assertEqual(rc, 0, err)
        head = git(root, "rev-parse", "--short=7", "HEAD").strip()
        self.assertIn(f"owed c → t@example.com (since {head})", out)

    def test_verify_clears_an_owed_claim(self):
        root = self.store()
        write(root, "a.py", "two\n")
        run_cli(root, "owe", "c", "--to", "bob@example.com")
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        text = (root / "claims" / "c.md").read_text()
        self.assertIn("status: verified", text)
        self.assertNotIn("owed_by", text)


if __name__ == "__main__":
    unittest.main()
