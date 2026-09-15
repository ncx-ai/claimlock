"""A verification pins a SET of contents, not independent files: `verify`
writes a `pins:` digest of the whole pin set, so two branches that re-verify
different sources of one claim conflict instead of merging into a combination
nobody verified (teams spec T10, implemented as T13)."""
import re
import shutil
import subprocess
import unittest

from helpers import TmpCase, claim_text, clone, git, init_bare, make_repo, pinned_text, run_cli, write
from claimlock.claims import Source, pin_digest
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def _claim(root):
    return (root / "claims" / "c.md").read_text()


def _edit(root, edit):
    p = root / "claims" / "c.md"
    p.write_text(edit(p.read_text()))


class DigestOnVerify(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "one\n")
        write(self.root, "b.py", "two\n")
        write(self.root, "claims/c.md", claim_text("c", sources=("a.py", "b.py")))
        self.a, self.b = blob_of_bytes(b"one\n"), blob_of_bytes(b"two\n")

    def verify(self):
        rc, out, err = run_cli(self.root, "verify", "c")
        self.assertEqual(rc, 0, out + err)

    def test_verify_writes_the_digest_after_the_sources_block(self):
        self.verify()
        digest = pin_digest([Source("a.py", self.a), Source("b.py", self.b)])
        self.assertIn(f"  - path: b.py\n    blob: {self.b}\npins: {digest}\n---\n", _claim(self.root))
        self.assertEqual(run_cli(self.root, "check")[0], 0)

    def test_re_verify_replaces_the_digest(self):
        self.verify()
        write(self.root, "a.py", "changed\n")
        self.verify()
        text = _claim(self.root)
        self.assertEqual(text.count("\npins: "), 1)
        self.assertIn(f"pins: {pin_digest([Source('a.py', blob_of_bytes(b'changed' + bytes([10]))), Source('b.py', self.b)])}",
                      text)

    def test_the_digest_ignores_source_order(self):
        self.assertEqual(pin_digest([Source("a", "1" * 40), Source("b", "2" * 40)]),
                         pin_digest([Source("b", "2" * 40), Source("a", "1" * 40)]))
        self.verify()
        a_entry, b_entry = f"  - path: a.py\n    blob: {self.a}\n", f"  - path: b.py\n    blob: {self.b}\n"
        _edit(self.root, lambda t: t.replace(a_entry + b_entry, b_entry + a_entry))
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 0, out)

    def test_a_hand_added_source_breaks_the_digest(self):
        self.verify()
        write(self.root, "c.py", "three\n")
        _edit(self.root, lambda t: t.replace("\npins: ", "\n  - path: c.py\npins: "))
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn("digest does not match", out)
        # verify is the repair, so a stale digest never stops it.
        self.verify()
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 0, out)

    def test_a_hand_edited_pin_breaks_the_digest(self):
        self.verify()
        other = blob_of_bytes(b"something else\n")
        write(self.root, "a.py", "something else\n")
        _edit(self.root, lambda t: t.replace(f"blob: {self.a}", f"blob: {other}"))
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn("digest does not match", out)

    def test_a_malformed_digest_is_invalid(self):
        self.verify()
        _edit(self.root, lambda t: t.replace("\npins: ", "\npins: nothex # was: "))
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn("'pins' must be", out)

    def test_a_claim_without_a_digest_is_still_valid(self):
        write(self.root, "claims/c.md", pinned_text("c", [("a.py", self.a), ("b.py", self.b)]))
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 0, out)
        self.verify()
        self.assertIn("\npins: ", _claim(self.root), "the next verify adds the digest")


@NEED_GIT
class ReverificationsOnTwoBranches(TmpCase):
    def setUp(self):
        super().setUp()
        bare = self.tmp / "origin.git"
        init_bare(bare)
        self.amy, self.ben = self.tmp / "amy", self.tmp / "ben"
        clone(bare, self.amy, "amy@example.com")
        run_cli(self.amy, "init")
        write(self.amy, "a.py", "A1\n")
        write(self.amy, "b.py", "B1\n")
        write(self.amy, "claims/c.md", claim_text("c", sources=("a.py", "b.py")))
        git(self.amy, "add", "-A")
        self.assertEqual(run_cli(self.amy, "verify", "c")[0], 0)
        git(self.amy, "add", "-A")
        git(self.amy, "commit", "-qm", "c")
        git(self.amy, "push", "-q", "origin", "main")
        clone(bare, self.ben, "ben@example.com")

    def reverify(self, root, rel, content, push):
        write(root, rel, content)
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "commit", "-qam", f"{rel}")
        if push:
            git(root, "push", "-q", "origin", "main")

    def pull_ben(self):
        return subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.ben,
                              capture_output=True, text=True)

    def test_disjoint_reverifications_conflict_and_resolve_to_owed(self):
        self.reverify(self.amy, "a.py", "A2\n", push=True)
        self.reverify(self.ben, "b.py", "B2\n", push=False)
        pull = self.pull_ben()
        self.assertNotEqual(pull.returncode, 0, "the digest line must conflict")
        self.assertEqual(((self.ben / "a.py").read_text(), (self.ben / "b.py").read_text()), ("A2\n", "B2\n"),
                         "precondition: the sources merged cleanly into a combination nobody verified")
        rc, out, err = run_cli(self.ben, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("OWED", out)
        self.assertIn("never checked them together", out)
        text = _claim(self.ben)
        self.assertIn("status: owed", text)
        self.assertIn('owed_by: "ben@example.com"', text)
        self.assertNotIn("\npins: ", text, "nobody verified this pin set as a whole")
        self.assertNotIn("<<<<<<<", text)
        git(self.ben, "add", "-A")
        rc, out, _ = run_cli(self.ben, "check")
        self.assertNotIn("INVALID", out)

    def test_a_side_without_a_digest_cannot_smuggle_in_a_combination(self):
        # Ben's branch still runs a claimlock that writes no `pins:` line. His
        # re-pin of b.py sits next to Amy's new digest line, so the merge
        # conflicts there, while Amy's a.py pin merges cleanly into BOTH
        # conflict sides. Read from the conflicted file, Ben's side looks like
        # a whole pin set matching the merged content; only his real version
        # (index stage 3) shows he never verified A2.
        def legacy_repin(text):
            old = re.search(r"  - path: b.py\n    blob: ([0-9a-f]{40})", text).group(1)
            return re.sub(r"\npins: [0-9a-f]{40}", "", text).replace(old, blob_of_bytes(b"B2\n"))

        self.reverify(self.amy, "a.py", "A2\n", push=True)
        write(self.ben, "b.py", "B2\n")
        _edit(self.ben, legacy_repin)
        git(self.ben, "commit", "-qam", "legacy re-pin")
        self.assertNotEqual(self.pull_ben().returncode, 0, "precondition: the claim conflicts")
        self.assertEqual(((self.ben / "a.py").read_text(), (self.ben / "b.py").read_text()), ("A2\n", "B2\n"))
        rc, out, err = run_cli(self.ben, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("OWED", out)
        self.assertIn("status: owed", _claim(self.ben))

    def test_content_equal_to_one_whole_side_is_kept(self):
        self.reverify(self.amy, "a.py", "A2\n", push=True)
        self.reverify(self.ben, "a.py", "A3\n", push=False)
        self.assertNotEqual(self.pull_ben().returncode, 0)
        git(self.ben, "checkout", "--theirs", "a.py")
        rc, out, err = run_cli(self.ben, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("KEPT", out)
        git(self.ben, "add", "-A")
        rc, out, _ = run_cli(self.ben, "check")
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
