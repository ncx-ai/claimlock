import unittest

from helpers import TmpCase, claim_text, make_repo, run_cli, write
from claimlock import claims as C
from claimlock import evidence
from claimlock.project import load


class Locator(unittest.TestCase):
    def test_picks_the_longest_identifier_token(self):
        self.assertEqual(
            evidence.locator("boogy-host::grpc_edge::tests::a_detail_beyond_the_cap_is_dropped"),
            "a_detail_beyond_the_cap_is_dropped")

    def test_prose_naming_two_tests_returns_one_of_them(self):
        got = evidence.locator("pkg: one_long_test_name (both), another_long_test_name")
        self.assertIn(got, {"one_long_test_name", "another_long_test_name"})

    def test_no_token_long_enough_is_none(self):
        self.assertIsNone(evidence.locator("abc::xy"))


class Audit(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def _claims(self, root):
        return C.load_claims(load(root))

    def test_a_cited_test_that_exists_resolves(self):
        write(self.root, "src/lib.py", "def test_the_thing_resolves():\n    pass\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "test_the_thing_resolves"),)))
        checks, scanned, skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([(c.claim_id, c.outcome) for c in checks], [("a", "resolved")])
        self.assertGreaterEqual(scanned, 2)  # src/lib.py + claims/a.md at least
        self.assertEqual(skipped, 0)

    def test_renaming_the_test_makes_the_same_claim_unresolved(self):
        """The falsifier: without actually checking file contents, a scanner
        that always returns true would still pass the first assertion above."""
        write(self.root, "src/lib.py", "def test_the_thing_resolves():\n    pass\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "test_the_thing_resolves"),)))
        # Rename the test function in place — the claim still cites the old name.
        write(self.root, "src/lib.py", "def test_the_thing_was_renamed():\n    pass\n")
        checks, scanned, skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([(c.claim_id, c.outcome) for c in checks], [("a", "unresolved")])

    def test_measurement_ref_is_never_consulted_even_when_its_words_appear_nowhere(self):
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("measurement", "nowhere_to_be_found_at_all"),)))
        checks, scanned, skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual(checks, [])

    def test_a_ref_with_no_locator_is_unlocatable_not_a_failure(self):
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "abc xy"),)))
        checks, scanned, skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([(c.claim_id, c.outcome, c.locator) for c in checks], [("a", "unlocatable", None)])

    def test_a_file_over_the_ceiling_is_skipped_not_read(self):
        """The falsifier: a locator that exists only inside an oversized file
        must NOT resolve — if the guard silently read the file anyway (or
        merely truncated it while still finding the token), this would pass
        for the wrong reason."""
        needle = "test_findable_only_if_the_whole_file_is_read"
        # A non-identifier separator keeps `needle` its own token rather than
        # merging into one giant identifier with the padding that follows it.
        padding = "z" * evidence.MAX_SCAN_BYTES
        write(self.root, "vendor/blob.bin", needle + "\n" + padding)
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", needle),)))
        checks, scanned, skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([(c.claim_id, c.outcome) for c in checks], [("a", "unresolved")])
        self.assertGreaterEqual(skipped, 1)

    def test_a_file_at_or_under_the_ceiling_is_still_read(self):
        needle = "test_findable_right_at_the_edge_of_the_ceiling"
        # Pad so the file sits exactly at the ceiling — the boundary case.
        padding = "z" * (evidence.MAX_SCAN_BYTES - len(needle) - 1)
        write(self.root, "vendor/blob.txt", needle + "\n" + padding)
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", needle),)))
        checks, scanned, skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([(c.claim_id, c.outcome) for c in checks], [("a", "resolved")])
        self.assertEqual(skipped, 0)


class CLI(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def test_exit_1_when_anything_is_unresolved(self):
        write(self.root, "src/lib.py", "def test_present_and_correct():\n    pass\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "test_present_and_correct"),)))
        write(self.root, "claims/b.md", claim_text("b", evidence=(("test", "test_never_existed_at_all"),)))
        rc, out, _ = run_cli(self.root, "evidence")
        self.assertEqual(rc, 1, out)
        self.assertIn("UNRESOLVED b  test_never_existed_at_all", out)
        self.assertNotIn("UNRESOLVED a", out)

    def test_exit_0_when_only_unlocatable_remain(self):
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "xy"),)))
        rc, out, _ = run_cli(self.root, "evidence")
        self.assertEqual(rc, 0, out)
        self.assertIn("UNLOCATABLE a  xy", out)

    def test_census_names_the_scanned_file_count(self):
        write(self.root, "src/lib.py", "def test_present_and_correct():\n    pass\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "test_present_and_correct"),)))
        rc, out, _ = run_cli(self.root, "evidence")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"claimlock: 1 resolved, 0 unresolved, 0 unlocatable in \d+ files scanned$")

    def test_census_names_a_skipped_oversized_file_so_it_is_not_silently_invisible(self):
        write(self.root, "vendor/blob.bin", "z" * (evidence.MAX_SCAN_BYTES + 10))
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "xy"),)))
        rc, out, _ = run_cli(self.root, "evidence")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"claimlock: 0 resolved, 0 unresolved, 1 unlocatable in \d+ files scanned, "
                              r"1 skipped \(too large\)")

    def test_full_uncaps_the_listings(self):
        for i in range(25):
            write(self.root, f"claims/c{i:02d}.md",
                  claim_text(f"c{i:02d}", evidence=(("test", f"test_never_exists_{i:02d}"),)))
        rc, out, _ = run_cli(self.root, "evidence")
        self.assertEqual(rc, 1, out)
        listed = [line for line in out.splitlines() if line.startswith("UNRESOLVED")]
        self.assertEqual(len(listed), 20)
        self.assertIn("… and 5 more unresolved evidence refs — claimlock evidence --full", out)
        rc, out, _ = run_cli(self.root, "evidence", "--full")
        self.assertEqual(rc, 1, out)
        listed = [line for line in out.splitlines() if line.startswith("UNRESOLVED")]
        self.assertEqual(len(listed), 25)


if __name__ == "__main__":
    unittest.main()
