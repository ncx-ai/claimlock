import unittest
from unittest import mock

from helpers import TmpCase, claim_text, make_repo, run_cli, write
from claimlock import claims as C
from claimlock import evidence
from claimlock import runners
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


class ParseRef(unittest.TestCase):
    def test_an_explicit_ref_names_a_file_and_a_test(self):
        self.assertEqual(
            evidence.parse_ref("crates/a/src/main.rs::mod_tests::no_url_refuses"),
            ("crates/a/src/main.rs", "mod_tests::no_url_refuses"))
        self.assertEqual(
            evidence.parse_ref("packages/web-sdk/src/shell.test.ts::createShell > attributes a frame"),
            ("packages/web-sdk/src/shell.test.ts", "createShell > attributes a frame"))

    def test_a_prose_ref_is_not_an_explicit_ref(self):
        # Falls through to the locator rule rather than being mis-parsed.
        self.assertIsNone(evidence.parse_ref("shell.test.ts: 'attributes a frame'"))
        self.assertIsNone(evidence.parse_ref("pkg::mod::a_test_name"))

    def test_a_bare_filename_is_refused(self):
        # Two packages may hold shell.test.ts, so a name with no directory
        # cannot identify one. Spec §4.2.
        self.assertIsNone(evidence.parse_ref("shell.test.ts::createShell > attributes a frame"))


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

    def test_a_locator_in_many_files_is_unlocatable_not_resolved(self):
        # Four files all containing the token, one claim citing it. The token is
        # real and present; what it cannot do is identify a test.
        for i in range(4):
            write(self.root, f"src/f{i}.py", "def attributes(): pass\n")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "'attributes a frame to its pane'"),)))
        checks, _scanned, _skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([c.outcome for c in checks], ["unlocatable"])
        self.assertEqual(checks[0].reason, "ambiguous")

    def test_a_locator_in_one_file_still_resolves(self):
        # The control. Without it the assertion above is satisfied by a rule
        # that calls everything unlocatable.
        write(self.root, "src/a.py", "def flush_debits_conserves(): pass\n")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "pkg::mod::flush_debits_conserves"),)))
        checks, _scanned, _skipped = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([c.outcome for c in checks], ["resolved"])
        self.assertIsNone(checks[0].reason)

    def test_the_threshold_is_inclusive_at_its_boundary(self):
        # AMBIGUOUS_FILES files is still resolvable; one more is not. Pins the
        # boundary so a later refactor cannot move it silently by one.
        for n, expected in ((evidence.AMBIGUOUS_FILES, "resolved"), (evidence.AMBIGUOUS_FILES + 1, "unlocatable")):
            with self.subTest(files=n):
                root = make_repo(self.tmp / f"r{n}", use_git=False)
                for i in range(n):
                    write(root, f"src/g{i}.py", "def a_named_test_function(): pass\n")
                write(root, "claims/a.md",
                      claim_text("a", evidence=(("test", "pkg::a_named_test_function"),)))
                checks, _s, _k = evidence.audit(load(root), self._claims(root))
                self.assertEqual(checks[0].outcome, expected)

    def test_an_explicit_ref_that_names_a_real_test_in_its_file_resolves(self):
        # The control for the two failing cases below: without it, both could
        # pass for the wrong reason (e.g. an explicit ref always refusing).
        write(self.root, "src/a.py", "def a_named_test_function(): pass\n")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "src/a.py::a_named_test_function"),)))
        checks, _s, _k = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([(c.outcome, c.locator) for c in checks],
                          [("matched", "src/a.py::a_named_test_function")])

    def test_an_explicit_ref_whose_file_cannot_be_decoded_is_unlocatable(self):
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "a.bin").write_bytes(b"\xff\xfe\x00not utf-8")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "src/a.bin::a_named_test_function"),)))
        checks, _s, _k = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual([(c.outcome, c.reason) for c in checks], [("unlocatable", "unreadable")])

    def test_an_explicit_ref_whose_file_is_missing_is_unresolved(self):
        write(self.root, "src/a.py", "def a_named_test_function(): pass\n")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "src/nope.py::a_named_test_function"),)))
        checks, _s, _k = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual(checks[0].outcome, "unresolved")

    def test_an_explicit_ref_whose_name_is_absent_from_its_file_is_unresolved(self):
        # The file exists and contains OTHER tests. The point of an explicit ref
        # is that the name is checked in THAT file, not anywhere in the repo.
        write(self.root, "src/a.py", "def a_named_test_function(): pass\n")
        write(self.root, "src/b.py", "def some_other_test_function(): pass\n")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "src/b.py::a_named_test_function"),)))
        checks, _s, _k = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual(checks[0].outcome, "unresolved")

    def test_a_ref_with_no_locator_keeps_its_own_reason(self):
        # "no-locator" and "ambiguous" are both unlocatable and must stay
        # distinguishable: one is a ref claimlock cannot parse, the other a ref
        # it parsed and cannot use.
        write(self.root, "src/a.py", "x = 1\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "a b c"),)))
        checks, _s, _k = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual(checks[0].outcome, "unlocatable")
        self.assertEqual(checks[0].reason, "no-locator")

    def test_a_statically_matched_explicit_ref_is_not_resolved(self):
        # The file exists and the name is in it, but no runner was asked. That
        # is weaker than a runner listing the test, and printing the two the
        # same is the defect this whole change is about, in a milder form.
        write(self.root, "src/a.py", "def a_named_test_function(): pass\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "src/a.py::a_named_test_function"),)))
        checks, _s, _k = evidence.audit(load(self.root), self._claims(self.root))
        self.assertEqual(checks[0].outcome, "matched")


class AskRunners(TmpCase):
    """`resolved` for an explicit ref means a runner listed that test. These
    tests replace the runner rather than installing one: what is under test is
    the wiring — which refs reach a runner, how often, and what each of its
    three answers does to the outcome."""

    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        # The name is present in the file, so the STATIC outcome is `matched`
        # for every case below; only the runner's answer varies.
        write(self.root, "src/a.test.ts", "it('a listed case', () => {})\n")

    def _claims(self):
        return C.load_claims(load(self.root))

    def _cite(self, name, cid="a"):
        write(self.root, f"claims/{cid}.md",
              claim_text(cid, evidence=(("test", f"src/a.test.ts::{name}"),)))

    def _audit(self, ask_runners=True):
        return evidence.audit(load(self.root), self._claims(), ask_runners=ask_runners)[0]

    def test_a_runner_that_lists_the_test_makes_an_explicit_ref_resolved(self):
        self._cite("a listed case")
        with mock.patch.object(runners, "vitest_tests",
                               return_value={("src/a.test.ts", "a listed case")}):
            checks = self._audit()
        self.assertEqual([c.outcome for c in checks], ["resolved"])

    def test_a_runner_that_does_not_list_the_test_makes_it_unresolved(self):
        """The falsifier. The name IS in the file — Task 3 calls that `matched`
        — so a wiring that promoted everything the runner was asked about would
        still pass the test above."""
        self._cite("a listed case")
        with mock.patch.object(runners, "vitest_tests",
                               return_value={("src/a.test.ts", "some other case")}):
            checks = self._audit()
        self.assertEqual([c.outcome for c in checks], ["unresolved"])

    def test_a_runner_that_lists_the_name_against_another_file_does_not_resolve_it(self):
        # The pair is (file, name): an explicit ref is resolved by THAT file's
        # listing, which is the whole point of naming a file (Task 2).
        self._cite("a listed case")
        with mock.patch.object(runners, "vitest_tests",
                               return_value={("src/b.test.ts", "a listed case")}):
            checks = self._audit()
        self.assertEqual([c.outcome for c in checks], ["unresolved"])

    def test_a_runner_that_cannot_be_consulted_keeps_the_static_outcome(self):
        # An absent toolchain is not a false claim.
        self._cite("a listed case")
        with mock.patch.object(runners, "vitest_tests", return_value=None):
            checks = self._audit()
        self.assertEqual([c.outcome for c in checks], ["matched"])

    def test_a_file_no_runner_owns_keeps_the_static_outcome(self):
        write(self.root, "src/a.py", "def a_named_test_function(): pass\n")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "src/a.py::a_named_test_function"),)))
        with mock.patch.object(runners, "vitest_tests") as vt, \
             mock.patch.object(runners, "cargo_tests") as ct:
            checks = self._audit()
        self.assertEqual([c.outcome for c in checks], ["matched"])
        vt.assert_not_called()
        ct.assert_not_called()

    def test_a_runner_can_resolve_a_name_the_static_scan_could_not_see(self):
        # The reason `matched` is not `resolved`: a name built in a loop appears
        # nowhere in the file as a literal. Static says unresolved; the runner
        # says it exists, and the runner is right.
        write(self.root, "src/a.test.ts",
              "for (const n of CASES) it(`a case for ${n}`, () => {})\n")
        self._cite("a case for seven")
        with mock.patch.object(runners, "vitest_tests",
                               return_value={("src/a.test.ts", "a case for seven")}):
            checks = self._audit()
        self.assertEqual([c.outcome for c in checks], ["resolved"])
        # The control: without the runner the same ref is unresolved.
        self.assertEqual([c.outcome for c in self._audit(ask_runners=False)], ["unresolved"])

    def test_a_ref_naming_only_the_leaf_of_a_runners_path_resolves(self):
        # How a person cites a test: the leaf, not the whole describe chain
        # (vitest) or module path (cargo). The runner still says that test
        # exists in that file.
        for listed in ({("src/a.test.ts", "createShell > a listed case")},
                       {("src/a.test.ts", "shell_tests::a listed case")}):
            with self.subTest(listed=listed):
                self._cite("a listed case")
                with mock.patch.object(runners, "vitest_tests", return_value=listed):
                    checks = self._audit()
                self.assertEqual([c.outcome for c in checks], ["resolved"])

    def test_a_leaf_must_be_a_whole_segment_of_the_runners_path(self):
        # The falsifier for the rule above: a suffix that is not a whole
        # segment is not the test. Without the separator, "case" would resolve
        # against "a listed case" and citing a word would be enough.
        self._cite("case")
        with mock.patch.object(runners, "vitest_tests",
                               return_value={("src/a.test.ts", "createShell > a listed case")}):
            checks = self._audit()
        self.assertEqual([c.outcome for c in checks], ["unresolved"])

    def test_a_prose_ref_never_reaches_a_runner(self):
        # Prose goes through Task 1's locator rule. It names no file, so there
        # is no package root to consult and nothing a runner could be asked.
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "a_listed_case_token"),)))
        with mock.patch.object(runners, "for_file") as ff:
            checks = self._audit()
        ff.assert_not_called()
        self.assertEqual([c.outcome for c in checks], ["unresolved"])

    def test_a_runner_is_consulted_once_per_file(self):
        # A store citing many tests in one file must not spawn one subprocess
        # per ref: 40 refs, one file, one consultation.
        for i in range(40):
            write(self.root, f"claims/c{i:02d}.md",
                  claim_text(f"c{i:02d}", evidence=(("test", f"src/a.test.ts::case {i:02d}"),)))
        with mock.patch.object(runners, "vitest_tests",
                               return_value=set()) as vt:
            checks = self._audit()
        self.assertEqual(vt.call_count, 1)
        self.assertEqual(len(checks), 40)

    def test_ask_runners_off_consults_nothing(self):
        self._cite("a listed case")
        with mock.patch.object(runners, "for_file") as ff:
            checks = self._audit(ask_runners=False)
        ff.assert_not_called()
        self.assertEqual([c.outcome for c in checks], ["matched"])

    def test_a_missing_file_is_unresolved_without_asking_a_runner(self):
        # There is nothing to ask about a file that does not exist, and the
        # package-root walk would start from a directory that may not either.
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "src/nope.test.ts::a listed case"),)))
        with mock.patch.object(runners, "for_file") as ff:
            checks = self._audit()
        ff.assert_not_called()
        self.assertEqual([c.outcome for c in checks], ["unresolved"])


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
        self.assertIn("UNLOCATABLE a  (no-locator)  xy", out)

    def test_census_names_the_scanned_file_count(self):
        write(self.root, "src/lib.py", "def test_present_and_correct():\n    pass\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "test_present_and_correct"),)))
        rc, out, _ = run_cli(self.root, "evidence")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"claimlock: 1 resolved, 0 matched, 0 unresolved, 0 unlocatable in \d+ files scanned$")

    def test_census_names_a_skipped_oversized_file_so_it_is_not_silently_invisible(self):
        write(self.root, "vendor/blob.bin", "z" * (evidence.MAX_SCAN_BYTES + 10))
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "xy"),)))
        rc, out, _ = run_cli(self.root, "evidence")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"claimlock: 0 resolved, 0 matched, 0 unresolved, 1 unlocatable in \d+ files scanned, "
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

    def test_ask_runners_says_when_it_had_nothing_to_ask(self):
        # The trap this line exists for: a store whose refs are all prose gets
        # BYTE-IDENTICAL output with and without the flag, which reads as "the
        # flag is broken" rather than "no ref names a file". Measured on a real
        # 140-claim store: 549 kind: test refs, none of them explicit.
        write(self.root, "src/lib.py", "def test_present_and_correct():\n    pass\n")
        write(self.root, "claims/a.md", claim_text("a", evidence=(("test", "test_present_and_correct"),)))
        rc, plain, _ = run_cli(self.root, "evidence")
        rc2, asked, _ = run_cli(self.root, "evidence", "--ask-runners")
        self.assertEqual((rc, rc2), (0, 0), asked)
        self.assertNotIn("nothing to ask", plain)
        self.assertIn("claimlock: --ask-runners had nothing to ask: no evidence ref names a file",
                      asked)

    def test_ask_runners_is_silent_when_a_ref_names_a_file(self):
        # The control. There is no vitest package here, so the runner cannot be
        # consulted and `matched` stands — but the ref WAS askable, so the note
        # must not print.
        write(self.root, "src/a.test.ts", "it('a listed case', () => {})\n")
        write(self.root, "claims/a.md",
              claim_text("a", evidence=(("test", "src/a.test.ts::a listed case"),)))
        rc, out, _ = run_cli(self.root, "evidence", "--ask-runners")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("nothing to ask", out)
        self.assertIn("0 resolved, 1 matched", out)

    def test_matched_does_not_fail_the_gate(self):
        # cmd_evidence exits 1 for unresolved ONLY.
        self.assertEqual(evidence.exit_code([evidence.Check("c", "r", "l", "matched")]), 0)
        self.assertEqual(evidence.exit_code([evidence.Check("c", "r", "l", "unlocatable", "ambiguous")]), 0)
        self.assertEqual(evidence.exit_code([evidence.Check("c", "r", "l", "unresolved")]), 1)


if __name__ == "__main__":
    unittest.main()
