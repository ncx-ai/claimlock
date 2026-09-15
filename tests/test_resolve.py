"""Two clones pin the same claim to different content; the merge conflicts on
the claim's pins; `claimlock resolve` keeps only a pin matching the merged
source, else hands the claim off as owed by the merger."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import TmpCase, claim_text, clone, git, init_bare, run_cli, write
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


NO_IDENTITY = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _edit_claim(root, edit):
    p = root / "claims" / "c.md"
    p.write_text(edit(p.read_text()))


def _pull(root):
    return subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=root, capture_output=True, text=True)


def _blob(content):
    return subprocess.run(["git", "hash-object", "--stdin"], input=content, capture_output=True,
                          text=True, check=True).stdout.strip()


@NEED_GIT
class ResolveAfterMerge(TmpCase):
    def setUp(self):
        super().setUp()
        bare = self.tmp / "origin.git"
        init_bare(bare)
        self.a, self.b = self.tmp / "a", self.tmp / "b"
        clone(bare, self.a, "amy@example.com")
        run_cli(self.a, "init")
        write(self.a, "src.py", "MAX = 1\n")
        write(self.a, "claims/c.md", claim_text("c", sources=("src.py",)))
        run_cli(self.a, "verify", "c")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "c")
        git(self.a, "push", "-q", "origin", "main")
        clone(bare, self.b, "ben@example.com")

    def both_verify(self, a_content, b_content, a_edit=None, b_edit=None):
        write(self.a, "src.py", a_content)
        run_cli(self.a, "verify", "c")
        if a_edit:
            _edit_claim(self.a, a_edit)
        git(self.a, "commit", "-qam", "a")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "src.py", b_content)
        run_cli(self.b, "verify", "c")
        if b_edit:
            _edit_claim(self.b, b_edit)
        git(self.b, "commit", "-qam", "b")
        return _pull(self.b)

    def share_claim(self, edit):
        """A commits an edit to the claim; B fast-forwards to it (the merge base)."""
        _edit_claim(self.a, edit)
        git(self.a, "commit", "-qam", "shared")
        git(self.a, "push", "-q", "origin", "main")
        git(self.b, "pull", "-q", "origin", "main")

    def diverge_claim(self, a_edit, b_edit):
        for root, edit in ((self.a, a_edit), (self.b, b_edit)):
            _edit_claim(root, edit)
            git(root, "commit", "-qam", "edit")
        git(self.a, "push", "-q", "origin", "main")
        return _pull(self.b)

    def assert_left_untouched(self, env=None):
        path = self.b / "claims" / "c.md"
        before = path.read_bytes()
        self.assertIn(b"<<<<<<<", before, "precondition: the merge must leave the claim conflicted")
        rc, out, err = run_cli(self.b, "resolve", env=env)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("LEFT", out)
        self.assertEqual(path.read_bytes(), before, "a LEFT claim must not be rewritten")
        return out

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

    def test_a_body_sources_line_does_not_make_a_body_conflict_resolvable(self):
        self.share_claim(lambda t: t.replace("The thing holds.", "The thing holds.\n\nsources: notes\n  - item"))
        pull = self.diverge_claim(lambda t: t.replace("  - item", "  - amy item"),
                                  lambda t: t.replace("  - item", "  - ben item"))
        self.assertNotEqual(pull.returncode, 0, "precondition: the body edits must conflict")
        self.assert_left_untouched()

    def test_a_body_conflict_on_sources_lines_is_left(self):
        self.share_claim(lambda t: t.replace("The thing holds.", "The thing holds.\n\nsources: notes"))
        pull = self.diverge_claim(lambda t: t.replace("sources: notes", "sources: amy notes"),
                                  lambda t: t.replace("sources: notes", "sources: ben notes"))
        self.assertNotEqual(pull.returncode, 0, "precondition: the body edits must conflict")
        self.assert_left_untouched()

    def test_a_sources_hunk_beside_a_body_hunk_is_left(self):
        self.share_claim(lambda t: t.replace("The thing holds.", "The thing holds.\n\nOne.\n\nTwo.\n\nLast line."))
        pull = self.both_verify("MAX = 2\n", "MAX = 3\n",
                                a_edit=lambda t: t.replace("Last line.", "Amy's last line."),
                                b_edit=lambda t: t.replace("Last line.", "Ben's last line."))
        self.assertNotEqual(pull.returncode, 0)
        text = (self.b / "claims" / "c.md").read_text()
        self.assertEqual(len(hunks(text.split("\n"))), 2, "precondition: one pin hunk and one body hunk")
        git(self.b, "checkout", "--theirs", "src.py")  # the pin hunk alone would be KEPT
        self.assert_left_untouched()

    def test_a_hunk_spanning_evidence_into_sources_is_left(self):
        # A real merge: both sides change the evidence ref AND re-pin, and git
        # joins the two edits into one hunk whose sides each run from a
        # `    ref:` line down through an identical `sources:` block. Resolving
        # it would silently take "ours" for the evidence conflict (spec §5.3).
        pull = self.both_verify("MAX = 2\n", "MAX = 3\n",
                                a_edit=lambda t: t.replace("ref: suite::case", "ref: t::theirs"),
                                b_edit=lambda t: t.replace("ref: suite::case", "ref: t::ours"))
        self.assertNotEqual(pull.returncode, 0)
        text = (self.b / "claims" / "c.md").read_text()
        [h] = hunks(text.split("\n"))
        self.assertEqual((h.ours[0], h.theirs[0]), ("    ref: t::ours", "    ref: t::theirs"),
                         "precondition: one hunk starting in evidence")
        self.assertIn("sources:", h.ours, "precondition: the hunk reaches into sources")
        git(self.b, "checkout", "--ours", "src.py")  # the pins alone would be KEPT
        self.assert_left_untouched()

    def test_no_identity_leaves_an_unmatched_source(self):
        self.both_verify("MAX = 2\n", "MAX = 3\n")
        git(self.b, "config", "--unset", "user.email")
        write(self.b, "src.py", "MAX = 4\n")
        self.assert_left_untouched(env=NO_IDENTITY)

    def test_an_invalid_git_email_is_no_identity(self):
        self.both_verify("MAX = 2\n", "MAX = 3\n")
        git(self.b, "config", "user.email", "ben")
        write(self.b, "src.py", "MAX = 4\n")
        out = self.assert_left_untouched(env=NO_IDENTITY)
        self.assertIn("not an email address", out)

    def test_diff3_base_content_never_keeps_the_base_pin(self):
        git(self.b, "config", "merge.conflictStyle", "diff3")
        self.assertNotEqual(self.both_verify("MAX = 2\n", "MAX = 3\n").returncode, 0)
        path = self.b / "claims" / "c.md"
        self.assertIn("|||||||", path.read_text(), "precondition: a diff3 base section")
        base, ours = _blob("MAX = 1\n"), _blob("MAX = 3\n")
        self.assertIn(base, path.read_text(), "precondition: the base pin is in the conflict")
        write(self.b, "src.py", "MAX = 1\n")
        rc, out, err = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("OWED", out)
        text = path.read_text()
        self.assertNotIn(base, text)
        self.assertIn(ours, text)

    def test_sides_citing_different_sources_are_left(self):
        # A replaces the cited path inside the same lines B re-pins, so the
        # path change lands in the conflict hunk (an insertion elsewhere in
        # the block would merge cleanly and both sides would cite it).
        write(self.a, "other.py", "x\n")
        _edit_claim(self.a, lambda t: t.replace("  - path: src.py", "  - path: other.py"))
        self.assertEqual(run_cli(self.a, "verify", "c")[0], 0)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "a")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "src.py", "MAX = 3\n")
        run_cli(self.b, "verify", "c")
        git(self.b, "commit", "-qam", "b")
        self.assertNotEqual(_pull(self.b).returncode, 0)
        self.assert_left_untouched()


if __name__ == "__main__":
    unittest.main()
