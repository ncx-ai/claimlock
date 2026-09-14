import unittest

from helpers import TmpCase, claim_text, make_repo, run_cli, write
from claimlock.claims import evaluate, load_claims, open_hasher, problems
from claimlock.project import load

SRC = ("a.py",)


def owed(cid, owed_by="bob@example.com", owed_since="a1b2c3d"):
    lines = []
    if owed_by is not None:
        lines.append(f"owed_by: {owed_by}")
    if owed_since is not None:
        lines.append(f"owed_since: {owed_since}")
    return claim_text(cid, status="owed", sources=SRC, extra_lines=lines)


class OwedStatus(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "one\n")

    def probs(self, text):
        write(self.root, "claims/c.md", text)
        p = load(self.root)
        [c] = load_claims(p)
        return c, problems(c, p)

    def test_valid_owed_claim_has_no_problems_and_no_freshness(self):
        c, got = self.probs(owed("c"))
        self.assertEqual(got, [])
        self.assertEqual((c.owed_by, c.owed_since), ("bob@example.com", "a1b2c3d"))
        [r] = evaluate(load(self.root), open_hasher(load(self.root)))
        self.assertIsNone(r.state)

    def test_owed_rules(self):
        cases = [
            ("no-owner", owed("c", owed_by=None), "not an email address"),
            ("bad-owner", owed("c", owed_by="bob"), "not an email address"),
            ("no-since", owed("c", owed_since=None), "'owed_since'"),
            ("bad-since", owed("c", owed_since="yesterday"), "'owed_since'"),
            ("none-since-ok", owed("c", owed_since="none"), None),
            ("owed-fields-on-verified", claim_text("c", status="verified", sources=SRC,
                                                   extra_lines=["owed_by: bob@example.com"]),
             "only valid with status: owed"),
        ]
        for name, text, needle in cases:
            with self.subTest(name):
                _, got = self.probs(text)
                if needle is None:
                    self.assertEqual(got, [])
                else:
                    self.assertTrue(any(needle in g for g in got), f"{needle!r} not in {got}")

    def test_legacy_verified_at_is_still_accepted(self):
        _, got = self.probs(claim_text("c", extra_lines=["verified_at: 2026-09-08T10:00:00-04:00"]))
        self.assertEqual(got, [])


class Conflicted(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def test_conflict_markers_make_the_claim_conflicted(self):
        text = ("---\nid: c\nstatus: verified\nevidence: []\nsources:\n  - path: a.py\n"
                "<<<<<<< HEAD\n    blob: " + "a" * 40 + "\n=======\n    blob: " + "b" * 40 +
                "\n>>>>>>> origin/main\n---\nHolds.\n")
        write(self.root, "claims/c.md", text)
        p = load(self.root)
        [c] = load_claims(p)
        self.assertTrue(c.conflicted)
        self.assertEqual(c.id, "c")
        [msg] = problems(c, p)
        self.assertIn("conflict markers", msg)
        self.assertIn("claimlock resolve", msg)
        [r] = evaluate(p, open_hasher(p))
        self.assertIsNone(r.state)

    def test_a_setext_heading_is_not_a_conflict(self):
        write(self.root, "claims/c.md", claim_text("c", body="Title\n=======\n\nText."))
        [c] = load_claims(load(self.root))
        self.assertFalse(c.conflicted)


class VerifyWritesNoTimestamp(TmpCase):
    def test_verify_is_byte_identical_when_nothing_changed_and_clears_owed_fields(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=SRC, extra_lines=["verified_at: 2026-01-01"]))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        first = (root / "claims" / "c.md").read_bytes()
        self.assertNotIn(b"verified_at", first)
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual((root / "claims" / "c.md").read_bytes(), first)
        text = first.decode().replace("status: verified", "status: owed\nowed_by: bob@example.com\nowed_since: none")
        write(root, "claims/c.md", text)
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual((root / "claims" / "c.md").read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
