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
        self.assertRegex(text, r"verified_at: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\n")
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
        for cid, text in cases.items():
            with self.subTest(cid):
                rc, _, err = run_cli(self.root, "verify", cid)
                self.assertEqual(rc, 1)
                self.assertIn(cid, err)
                self.assertEqual((self.root / f"claims/{cid}.md").read_text(), text)
        rc, _, err = run_cli(self.root, "verify", "unknown")
        self.assertEqual(rc, 1)
        self.assertIn("no claim 'unknown'", err)

    def test_reverify_prunes_the_old_snapshot(self):
        write(self.root, "claims/c.md", verifiable("c"))
        run_cli(self.root, "verify", "c")
        objects = self.root / ".claimlock" / "objects"
        self.assertEqual([p.name for p in objects.iterdir()], [blob_of_bytes(b"one\n")])
        write(self.root, "a.py", "two!\n")
        run_cli(self.root, "verify", "c")
        self.assertEqual([p.name for p in objects.iterdir()], [blob_of_bytes(b"two!\n")])


class Diff(TmpCase):
    def pin_then_edit(self, root):
        write(root, "a.py", "one\nkeep\n")
        write(root, "claims/c.md", verifiable("c"))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        write(root, "a.py", "two\nkeep\n")

    def test_diff_from_snapshot_without_git(self):
        root = make_repo(self.tmp / "r", use_git=False)
        self.pin_then_edit(root)
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE    c", out)
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, err)
        self.assertIn("-one", out)
        self.assertIn("+two", out)
        self.assertIn("(verified)", out)

    @NEED_GIT
    def test_diff_from_git_object_and_no_snapshot_written(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\nkeep\n")
        git(root, "add", "a.py")
        git(root, "commit", "-q", "-m", "a")
        write(root, "claims/c.md", verifiable("c"))
        run_cli(root, "verify", "c")
        objects = root / ".claimlock" / "objects"
        self.assertFalse(objects.exists() and any(objects.iterdir()))
        write(root, "a.py", "two\nkeep\n")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertIn("+two", out)

    def test_unavailable_prior_content_says_so(self):
        root = make_repo(self.tmp / "r", use_git=False)
        self.pin_then_edit(root)
        shutil.rmtree(root / ".claimlock" / "objects")
        rc, out, _ = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0)
        self.assertIn("unavailable", out)

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

    def test_corrupt_snapshot_is_unavailable(self):
        root = make_repo(self.tmp / "r", use_git=False)
        self.pin_then_edit(root)
        [obj] = list((root / ".claimlock" / "objects").iterdir())
        obj.write_bytes(b"tampered\n")
        self.assertIn("unavailable", run_cli(root, "diff", "c")[1])


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
