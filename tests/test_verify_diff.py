import shutil
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def verifiable(cid, sources=("a.py",)):
    return claim_text(cid, sources=sources, body="The thing holds.\n\nBecause reasons.")


class Verify(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "one\n")

    def test_pins_sets_status_and_preserves_body(self):
        write(self.root, "claims/c.md", verifiable("c"))
        rc, out, err = run_cli(self.root, "verify", "c")
        self.assertEqual(rc, 0, err)
        blob = blob_of_bytes(b"one\n")
        self.assertIn(f"a.py @ {blob[:12]}", out)
        text = (self.root / "claims/c.md").read_text()
        self.assertIn("status: verified\n", text)
        self.assertNotIn("verified_at", text)
        self.assertIn(f"  - path: a.py\n    blob: {blob}\n", text)
        self.assertTrue(text.endswith("---\nThe thing holds.\n\nBecause reasons.\n"))
        self.assertEqual(run_cli(self.root, "check")[0], 0)

    def test_refusals_leave_the_file_untouched(self):
        cases = {
            "no-evidence": claim_text("no-evidence", evidence=(), sources=("a.py",)),
            "no-sources": claim_text("no-sources"),
            "refuted": claim_text("refuted", status="refuted", sources=("a.py",)),
            "gone": claim_text("gone", sources=("deleted.py",)),
        }
        for cid, text in cases.items():
            write(self.root, f"claims/{cid}.md", text)
        errs = {}
        for cid, text in cases.items():
            with self.subTest(cid):
                rc, _, err = run_cli(self.root, "verify", cid)
                self.assertEqual(rc, 1)
                self.assertIn(cid, err)
                self.assertEqual((self.root / f"claims/{cid}.md").read_text(), text)
                errs[cid] = err
        # The refusal for a missing source keeps the OS's own reason, not just
        # the generic "does not exist or cannot be read" text.
        self.assertIn("does not exist or cannot be read", errs["gone"])
        self.assertIn("No such file or directory", errs["gone"])
        rc, _, err = run_cli(self.root, "verify", "unknown")
        self.assertEqual(rc, 1)
        self.assertIn("no claim 'unknown'", err)


class Diff(TmpCase):
    def test_missing_fresh_and_unknown(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", verifiable("c"))
        run_cli(root, "verify", "c")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("is fresh", out)
        (root / "a.py").unlink()
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("a.py: does not exist or cannot be read", out)
        self.assertEqual(run_cli(root, "diff", "nope")[0], 1)


@NEED_GIT
class Transition(TmpCase):
    def test_pins_survive_git_init(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", verifiable("c"))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "t")
        git(root, "config", "commit.gpgsign", "false")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "adopt")
        self.assertEqual(run_cli(root, "check")[0], 0)
        write(root, "a.py", "two!\n")
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE    c", out)
        self.assertIn("+two!", run_cli(root, "diff", "c")[1])


if __name__ == "__main__":
    unittest.main()
