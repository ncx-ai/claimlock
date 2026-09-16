"""Task 1: `check` prints a bounded report by default — hints once, capped
claims and sources — and `check --json` carries only blocking (+ owed) claims
unless `--full` is given. Spec: docs/specs/2026-09-15-claimlock-output-budget-design.md §3.1/§3.2."""
import json
import unittest

from helpers import TmpCase, claim_text, make_repo, pinned_text, run_cli, write
from claimlock.cli import BODY_LINES, EVIDENCE_CHARS, HEADLINE_CHARS, HINT, LISTED_CLAIMS, SOURCE_LINES
from claimlock.pins import blob_of_bytes


class CheckStoreMixin:
    """A plain-directory store of `n_claims` claims, each with
    `sources_per_claim` sources, verified, then every source rewritten so
    every claim is blocking (stale)."""

    def store(self, n_claims, sources_per_claim=3, body_lines=4):
        root = make_repo(self.tmp / "r", use_git=False)
        body = "\n".join(f"line {k} of the body." for k in range(body_lines))
        ids = [f"claim-{i:03d}" for i in range(n_claims)]
        for cid in ids:
            srcs = [f"src/{cid}-{j}.py" for j in range(sources_per_claim)]
            for s in srcs:
                write(root, s, f"{s} — original\n")
            write(root, f"claims/{cid}.md", claim_text(cid, sources=srcs, body=body))
        rc, out, err = run_cli(root, "verify", *ids)
        assert rc == 0, (out, err)
        for cid in ids:
            for j in range(sources_per_claim):
                write(root, f"src/{cid}-{j}.py", f"src/{cid}-{j}.py — CHANGED\n")
        return root, ids


class DefaultCapOnFailingClaims(CheckStoreMixin, TmpCase):
    def setUp(self):
        super().setUp()
        self.root, self.ids = self.store(30)

    def test_lists_at_most_LISTED_CLAIMS_then_a_note(self):
        rc, out, err = run_cli(self.root, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(out.count("STALE    claim-"), LISTED_CLAIMS)
        self.assertIn("… and 10 more failing claims — claimlock check --full", out)

    def test_hints_block_has_exactly_one_stale_line_for_the_first_claim(self):
        rc, out, err = run_cli(self.root, "check")
        self.assertIn("hints:", out)
        expected = f"  stale: {HINT['stale'].format(id=self.ids[0])}"
        self.assertIn(expected, out)
        self.assertEqual(out.count(expected), 1)

    def test_no_hint_text_inside_a_verdict_block(self):
        rc, out, err = run_cli(self.root, "check")
        # The hint's static wording (id-independent part) must appear exactly
        # once in the whole output — in the hints: block, never per-claim.
        static_part = "re-check it (claimlock diff"
        self.assertEqual(out.count(static_part), 1)

    def test_exit_code_and_summary_report_all_30(self):
        rc, out, err = run_cli(self.root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("30 claims", out)
        self.assertIn("30 stale", out)


class SourceAndProblemCaps(CheckStoreMixin, TmpCase):
    def test_five_stale_sources_capped_at_three(self):
        root, ids = self.store(1, sources_per_claim=5)
        rc, out, err = run_cli(root, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("         … and 2 more sources — claimlock check --full", out)
        self.assertEqual(out.count(": stale"), SOURCE_LINES)
        # A single claim never trips the LISTED_CLAIMS cap, so this is the
        # only place `--full` can appear — it must still be reachable.
        self.assertIn("claimlock check --full", out)

    def test_five_problems_capped_at_three(self):
        root = make_repo(self.tmp / "r", use_git=False)
        src_lines = "\n".join(
            f"  - path: src/s{i}.py\n    blob: not-a-valid-blob-{i}" for i in range(5))
        text = (
            "---\n"
            "id: bad-claim\n"
            "area: core\n"
            "status: unverified\n"
            "evidence:\n"
            "  - kind: test\n"
            "    ref: s::c\n"
            "sources:\n"
            f"{src_lines}\n"
            "---\n"
            "Body.\n"
        )
        write(root, "claims/bad-claim.md", text)
        rc, out, err = run_cli(root, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("         … and 2 more problems — claimlock check --full", out)
        self.assertEqual(out.count("has a malformed blob"), SOURCE_LINES)
        self.assertIn("claimlock check --full", out)


class ExactCapBoundary(CheckStoreMixin, TmpCase):
    def test_exactly_LISTED_CLAIMS_prints_no_note(self):
        root, ids = self.store(LISTED_CLAIMS)
        rc, out, err = run_cli(root, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(out.count("STALE    claim-"), LISTED_CLAIMS)
        self.assertNotIn("more failing claims", out)

    def test_one_over_LISTED_CLAIMS_prints_the_note(self):
        root, ids = self.store(LISTED_CLAIMS + 1)
        rc, out, err = run_cli(root, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(out.count("STALE    claim-"), LISTED_CLAIMS)
        self.assertIn("… and 1 more failing claims — claimlock check --full", out)


class FullFlagRestoresEverything(CheckStoreMixin, TmpCase):
    def setUp(self):
        super().setUp()
        self.root, self.ids = self.store(30)

    def test_full_lists_every_claim_and_every_source_but_hints_once(self):
        rc, out, err = run_cli(self.root, "check", "--full")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(out.count("STALE    claim-"), 30)
        self.assertNotIn("more failing claims", out)
        self.assertEqual(out.count("hints:"), 1)
        expected = f"  stale: {HINT['stale'].format(id=self.ids[0])}"
        self.assertEqual(out.count(expected), 1)


class JsonFiltersToBlockingByDefault(CheckStoreMixin, TmpCase):
    def setUp(self):
        super().setUp()
        self.root, self.ids = self.store(30)
        write(self.root, "src/fresh-000.py", "content\n")
        write(self.root, "claims/fresh-000.md",
              claim_text("fresh-000", sources=["src/fresh-000.py"]))
        rc, out, err = run_cli(self.root, "verify", "fresh-000")
        assert rc == 0, (out, err)

    def test_default_omits_the_fresh_claim(self):
        rc, out, err = run_cli(self.root, "check", "--json")
        self.assertEqual(rc, 1, out + err)
        data = json.loads(out)
        self.assertEqual(data["claims"], 31)
        self.assertEqual(data["sources_hashed"], 91)
        self.assertEqual(len(data["results"]), 30)
        self.assertEqual(data["omitted"], 1)
        ids = {r["id"] for r in data["results"]}
        self.assertNotIn("fresh-000", ids)
        self.assertEqual(data["counts"]["stale"], 30)

    def test_full_includes_the_fresh_claim_with_omitted_zero(self):
        rc, out, err = run_cli(self.root, "check", "--json", "--full")
        self.assertEqual(rc, 1, out + err)
        data = json.loads(out)
        self.assertEqual(len(data["results"]), 31)
        self.assertEqual(data["omitted"], 0)
        ids = {r["id"] for r in data["results"]}
        self.assertIn("fresh-000", ids)


class OwedAndElsewhereListsAreCappedToo(TmpCase):
    """Spec §3.1: "The pre-existing (not changed here): list and the OWED
    lines keep their shape but obey LISTED_CLAIMS the same way." — not
    exercised by the required Step-1 tests, added for coverage."""

    def test_owed_list_capped_with_a_note(self):
        root = make_repo(self.tmp / "r", use_git=False)
        ids = [f"owed-{i:03d}" for i in range(LISTED_CLAIMS + 5)]
        for cid in ids:
            write(root, f"src/{cid}.py", "one\n")
            write(root, f"claims/{cid}.md",
                  pinned_text(cid, [(f"src/{cid}.py", blob_of_bytes(b"other\n"))]))
        rc, out, err = run_cli(root, "owe", *ids, "--to", "bob@example.com")
        self.assertEqual(rc, 0, out + err)
        rc, out, err = run_cli(root, "check")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.count("OWED     owed-"), LISTED_CLAIMS)
        self.assertIn("… and 5 more owed claims — claimlock check --full", out)
        rc, out, err = run_cli(root, "check", "--full")
        self.assertEqual(out.count("OWED     owed-"), LISTED_CLAIMS + 5)
        self.assertNotIn("more owed claims", out)


class JsonKeepsOwedByDefault(TmpCase):
    def test_owed_claim_is_in_results_by_default(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", pinned_text("c", [("a.py", blob_of_bytes(b"other\n"))]))
        rc, out, err = run_cli(root, "owe", "c", "--to", "bob@example.com")
        self.assertEqual(rc, 0, out + err)
        rc, out, err = run_cli(root, "check", "--json")
        self.assertEqual(rc, 0, out + err)
        data = json.loads(out)
        ids = {r["id"] for r in data["results"]}
        self.assertIn("c", ids)
        self.assertEqual(data["omitted"], 0)


class MixedStoreJsonBudget(TmpCase):
    """A store where most claims are fresh (the realistic case) is the real
    guard on `--json`'s filtering — an all-blocking fixture can't show it,
    since filtering then omits nothing (see BudgetCeilings.test_json_output_is_bounded)."""

    def _mixed_store(self, n_fresh, n_blocking):
        root = make_repo(self.tmp / "r", use_git=False)
        fresh_ids = [f"fresh-{i:03d}" for i in range(n_fresh)]
        blocking_ids = [f"blocking-{i:03d}" for i in range(n_blocking)]
        for cid in fresh_ids + blocking_ids:
            path = f"src/{cid}.py"
            write(root, path, f"{cid} v1\n")
            write(root, f"claims/{cid}.md", claim_text(cid, sources=[path]))
        rc, out, err = run_cli(root, "verify", *(fresh_ids + blocking_ids))
        assert rc == 0, (out, err)
        for cid in blocking_ids:
            write(root, f"src/{cid}.py", f"{cid} CHANGED\n")
        return root, fresh_ids, blocking_ids

    def test_default_json_is_bounded_and_holds_only_the_blocking_claims(self):
        root, fresh_ids, blocking_ids = self._mixed_store(24, 6)
        rc, out, err = run_cli(root, "check", "--json")
        self.assertEqual(rc, 1, out + err)
        data = json.loads(out)
        self.assertEqual({r["id"] for r in data["results"]}, set(blocking_ids))
        self.assertEqual(len(data["results"]), 6)
        self.assertEqual(data["omitted"], 24)
        size = len(out.encode("utf-8"))
        self.assertLessEqual(size, 3000, f"mixed-store default `check --json` was {size} bytes")

        rc, out_full, err = run_cli(root, "check", "--json", "--full")
        self.assertEqual(rc, 1, out_full + err)
        full_size = len(out_full.encode("utf-8"))
        self.assertLessEqual(size, full_size / 2,
                              f"default {size} B should be at most half of --full {full_size} B "
                              f"(24 fresh + 6 blocking claims)")


class BudgetCeilings(CheckStoreMixin, TmpCase):
    """Regression guards, not design targets (R1): if a measured default
    output exceeds these while the shape matches the spec, the ceiling is
    raised to the measured value rounded up rather than the format shrunk."""

    def test_default_check_output_is_bounded(self):
        root, ids = self.store(30)
        rc, out, err = run_cli(root, "check")
        size = len(out.encode("utf-8"))
        self.assertLessEqual(size, 4000, f"default `check` on 30 claims was {size} bytes")

    def test_json_output_is_bounded(self):
        # R1: this fixture makes every claim blocking (none fresh), so the
        # `omitted`-based filtering removes nothing here and the ceiling
        # reflects the full 30-result JSON payload, not the budget saving —
        # that saving is what test_default_omits_the_fresh_claim shows.
        # Measured 2026-09-15: 15,074 B; raised from the brief's 3,000 B
        # design target per R1 (the SHAPE matches spec §3.2; the number was a
        # target, not a ceiling this all-blocking fixture can meet).
        root, ids = self.store(30)
        rc, out, err = run_cli(root, "check", "--json")
        size = len(out.encode("utf-8"))
        self.assertLessEqual(size, 16000, f"`check --json` on 30 claims was {size} bytes")


class SearchDefaultShape(TmpCase):
    """Task 2, spec §3.3: `search` prints one line per hit by default, no
    indented body lines, no blank separator."""

    def test_one_line_per_hit_no_body_lines(self):
        root = make_repo(self.tmp / "r", use_git=False)
        body = "\n".join(f"needle appears on line {k}" for k in range(6))
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body=body))
        rc, out, err = run_cli(root, "search", "needle")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out, "c (core, unverified)  needle appears on line 0\n")

    def test_non_fresh_state_shown_before_the_two_spaces(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "orig\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="needle here"))
        rc, out, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        write(root, "src/a.py", "changed\n")
        rc, out, err = run_cli(root, "search", "needle")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out, "c (core, verified) [stale]  needle here\n")

    def test_200_char_headline_cut_to_120(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        long_line = "n" * 200
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body=long_line))
        rc, out, err = run_cli(root, "search", "n")
        self.assertEqual(rc, 0, out + err)
        expected_headline = long_line[:119] + "…"
        self.assertEqual(len(expected_headline), 120)
        self.assertEqual(out, f"c (core, unverified)  {expected_headline}\n")

    def test_no_match_message_and_exit_code_unchanged(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="something"))
        rc, out, err = run_cli(root, "search", "not-there")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(out, "claimlock: nothing matches 'not-there'\n")

    def test_a_hit_exits_zero(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="something"))
        rc, out, err = run_cli(root, "search", "something")
        self.assertEqual(rc, 0, out + err)


class SearchBodyFlagRestoresOldOutput(TmpCase):
    def test_body_flag_restores_indented_matching_lines(self):
        root = make_repo(self.tmp / "r", use_git=False)
        body = "needle one\nother\nneedle two"
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body=body))
        rc, out, err = run_cli(root, "search", "needle", "--body")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out, "c (core, unverified)\n    needle one\n    needle two\n\n")


class SearchBudgetCeiling(TmpCase):
    """Regression guard (R1), not a design target."""

    def test_search_matching_30_claims_is_bounded(self):
        root = make_repo(self.tmp / "r", use_git=False)
        for i in range(30):
            cid = f"claim-{i:03d}"
            write(root, f"src/{cid}.py", "x\n")
            write(root, f"claims/{cid}.md",
                  claim_text(cid, sources=[f"src/{cid}.py"],
                             body="needle appears here for every claim in this fixture."))
        rc, out, err = run_cli(root, "search", "needle")
        self.assertEqual(rc, 0, out + err)
        size = len(out.encode("utf-8"))
        self.assertLessEqual(size, 3000, f"default `search` matching 30 claims was {size} bytes")


class ShowBodyCap(TmpCase):
    def test_60_line_body_capped_at_40_then_a_note(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        body = "\n".join(f"line {k}" for k in range(60))
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body=body))
        rc, out, err = run_cli(root, "show", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("\n".join(f"line {k}" for k in range(BODY_LINES)), out)
        self.assertIn(f"… {60 - BODY_LINES} more lines — read claims/c.md", out)
        self.assertNotIn("line 40", out)


class ShowEvidenceCap(TmpCase):
    def test_500_char_ref_is_clipped_to_200(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        long_ref = "r" * 500
        write(root, "claims/c.md",
              claim_text("c", sources=["src/a.py"], evidence=(("test", long_ref),), body="Holds."))
        rc, out, err = run_cli(root, "show", "c")
        self.assertEqual(rc, 0, out + err)
        expected = long_ref[:EVIDENCE_CHARS - 1] + "…"
        self.assertEqual(len(expected), EVIDENCE_CHARS)
        self.assertIn(f"[test] {expected}", out)
        self.assertNotIn(long_ref, out)


class ShowUnchangedPartsWhenNotTruncated(TmpCase):
    """Spec §3.4: status, state, problems, the source list and `file:` are
    never truncated."""

    def test_status_state_sources_and_file_line_stay_the_same_shape(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "orig\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="Holds."))
        rc, out, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        write(root, "src/a.py", "changed\n")
        rc, out, err = run_cli(root, "show", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("c (core) — verified", out)
        self.assertIn("STALE: ", out)
        self.assertIn("Sources (a change here makes this claim stale):", out)
        self.assertIn("a.py — stale (", out)
        self.assertIn("\nfile: claims/c.md\n", out)

    def test_invalid_problem_lines_unchanged(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "claims/bad.md",
              "---\nid: bad\narea: core\nstatus: maybe\nevidence: []\nsources: []\n---\nBody.\n")
        rc, out, err = run_cli(root, "show", "bad")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("INVALID:", out)
        self.assertIn("status 'maybe'", out)


class ShowFullFlagRestoresWholeBodyAndRefs(TmpCase):
    def test_full_prints_whole_body_and_whole_refs(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        body = "\n".join(f"line {k}" for k in range(60))
        long_ref = "r" * 500
        write(root, "claims/c.md",
              claim_text("c", sources=["src/a.py"], evidence=(("test", long_ref),), body=body))
        rc, out, err = run_cli(root, "show", "c", "--full")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(body, out)
        self.assertNotIn("more lines", out)
        self.assertIn(f"[test] {long_ref}", out)
        self.assertNotIn("…", out)


class ShowBudgetCeiling(TmpCase):
    """Regression guard (R1), not a design target."""

    def test_60_line_body_and_three_500_char_refs_is_bounded(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        body = "\n".join(f"line {k} of the body." for k in range(60))
        evidence = tuple(("test", "r" * 500) for _ in range(3))
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], evidence=evidence, body=body))
        rc, out, err = run_cli(root, "show", "c")
        self.assertEqual(rc, 0, out + err)
        size = len(out.encode("utf-8"))
        self.assertLessEqual(size, 3000,
                              f"`show` with a 60-line body and three 500-char refs was {size} bytes")


class ListHeadlineTruncated(TmpCase):
    def test_200_char_headline_cut_to_120(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        long_line = "h" * 200
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body=long_line))
        rc, out, err = run_cli(root, "list")
        self.assertEqual(rc, 0, out + err)
        expected = long_line[:HEADLINE_CHARS - 1] + "…"
        self.assertIn(f"    {expected}", out)
        self.assertNotIn(long_line, out)

    def test_full_prints_headline_whole(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        long_line = "h" * 200
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body=long_line))
        rc, out, err = run_cli(root, "list", "--full")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"    {long_line}", out)


class ListStatusFlagLineUnchanged(TmpCase):
    def test_status_and_flag_line_unchanged(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "orig\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="Holds."))
        rc, out, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        write(root, "src/a.py", "changed\n")
        rc, out, err = run_cli(root, "list")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("✓ c (core) [stale]", out)


if __name__ == "__main__":
    unittest.main()
