import unittest

from helpers import TmpCase, claim_text, make_repo, write
from claimlock.claims import StoreMissing, evaluate, freshness, load_claims, open_hasher, problems
from claimlock.pins import Hasher, blob_of_bytes
from claimlock.project import load


def pinned_text(cid, pins, status="verified"):
    lines = ["---", f"id: {cid}", "area: core", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c", "sources:"]
    for path, blob in pins:
        lines.append(f"  - path: {path}")
        if blob:
            lines.append(f"    blob: {blob}")
    return "\n".join(lines + ["---", "Holds.", ""])


class Load(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def test_missing_dir_raises(self):
        (self.root / "claims").rmdir()
        with self.assertRaises(StoreMissing):
            load_claims(load(self.root))

    def test_readme_ignored_and_sorted(self):
        write(self.root, "claims/README.md", "# not a claim\n")
        write(self.root, "claims/b.md", claim_text("b"))
        write(self.root, "claims/a.md", claim_text("a"))
        self.assertEqual([c.id for c in load_claims(load(self.root))], ["a", "b"])

    def test_parse_error_becomes_the_only_problem(self):
        write(self.root, "claims/bad.md", "---\nid: bad\nsources: [x]\n---\nx\n")
        p = load(self.root)
        [c] = load_claims(p)
        self.assertEqual(c.id, "bad")
        [msg] = problems(c, p)
        self.assertIn("bad.md:3", msg)
        self.assertIn("unsupported", msg)

    def test_crlf_rejected(self):
        write(self.root, "claims/c.md", claim_text("c").replace("\n", "\r\n"))
        p = load(self.root)
        [c] = load_claims(p)
        self.assertIn("CR line endings", problems(c, p)[0])

    def test_properties(self):
        write(self.root, "claims/c.md", claim_text("c", sources=("a.py",), body="First line.\n\nMore."))
        [c] = load_claims(load(self.root))
        self.assertEqual((c.area, c.status, c.headline()), ("core", "unverified", "First line."))
        self.assertEqual([(s.path, s.blob) for s in c.sources], [("a.py", None)])


class Problems(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def probs(self, text, **kw):
        write(self.root, "claims/c.md", text)
        p = load(self.root)
        [c] = load_claims(p)
        return problems(c, p, **kw)

    def test_valid_unverified_claim_has_none(self):
        self.assertEqual(self.probs(claim_text("c")), [])

    def test_each_rule(self):
        cases = [
            ("unknown", claim_text("c", extra_lines=["color: red"]), "unknown field 'color'"),
            ("mismatch", claim_text("other"), "does not match filename"),
            ("no-id", "---\narea: x\n---\nbody\n", "missing 'id'"),
            ("status", claim_text("c", status="maybe"), "status 'maybe'"),
            ("body", claim_text("c", body=""), "no claim text"),
            ("kind", claim_text("c", evidence=(("vibes", "x"),)), "evidence kind 'vibes'"),
            ("ref", "---\nid: c\nevidence:\n  - kind: test\n---\nb\n", "needs 'kind' and 'ref'"),
            ("escape", claim_text("c", sources=("../outside.py",)), "stay inside"),
            ("dup", claim_text("c", sources=("a.py", "a.py")), "listed twice"),
            ("blob", "---\nid: c\nsources:\n  - path: a.py\n    blob: nothex\n---\nb\n", "malformed blob"),
            ("src-key", "---\nid: c\nsources:\n  - path: a.py\n    sha: x\n---\nb\n", "unknown keys"),
            ("no-evidence", claim_text("c", status="verified", evidence=(), sources=("a.py",)), "no evidence"),
            ("no-sources", claim_text("c", status="verified"), "can never go stale"),
        ]
        for name, text, needle in cases:
            with self.subTest(name):
                got = self.probs(text)
                self.assertTrue(any(needle in g for g in got), f"{needle!r} not in {got}")

    def test_as_status_applies_verified_rules(self):
        text = claim_text("c")
        self.assertEqual(self.probs(text), [])
        self.assertTrue(any("can never go stale" in g for g in self.probs(text, as_status="verified")))


class Freshness(TmpCase):
    def check(self, use_git):
        root = make_repo(self.tmp / f"r{use_git}", use_git=use_git)
        p = load(root)
        write(root, "a.py", "one\n")
        write(root, "b.py", "bee\n")
        blob_a = blob_of_bytes(b"one\n")
        blob_b = blob_of_bytes(b"bee\n")

        def state(text):
            write(root, "claims/c.md", text)
            [c] = load_claims(p)
            return freshness(c, p, open_hasher(p))

        self.assertEqual(state(pinned_text("c", [("a.py", blob_a)])), ("fresh", [("a.py", "fresh")]))
        self.assertEqual(state(pinned_text("c", [("a.py", None)])), ("unpinned", [("a.py", "unpinned")]))
        self.assertEqual(state(pinned_text("c", [("a.py", blob_a)], status="unverified")), (None, []))
        write(root, "a.py", "two!\n")
        self.assertEqual(state(pinned_text("c", [("a.py", blob_a)])), ("stale", [("a.py", "stale")]))
        (root / "b.py").unlink()
        self.assertEqual(state(pinned_text("c", [("a.py", blob_a), ("b.py", blob_b)])),
                         ("missing", [("a.py", "stale"), ("b.py", "missing")]))

    def test_without_git(self):
        self.check(False)

    def test_with_git(self):
        import shutil
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.check(True)

    def test_evaluate_counts_hashed_sources(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "b.py", "bee\n")
        write(root, "claims/c.md", pinned_text("c", [("a.py", None), ("b.py", None)]))
        write(root, "claims/d.md", claim_text("d"))
        p = load(root)
        h = Hasher(root, None)
        results = evaluate(p, h)
        self.assertEqual([(r.claim.id, r.state) for r in results], [("c", "unpinned"), ("d", None)])
        self.assertEqual(h.hashed, 2)


if __name__ == "__main__":
    unittest.main()
