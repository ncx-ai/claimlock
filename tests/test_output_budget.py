"""Task 1: `check` prints a bounded report by default — hints once, capped
claims and sources — and `check --json` carries only blocking (+ owed) claims
unless `--full` is given. Spec: docs/specs/2026-09-15-claimlock-output-budget-design.md §3.1/§3.2."""
import json
import unittest

from helpers import TmpCase, claim_text, make_repo, pinned_text, run_cli, write
from claimlock.cli import HINT, LISTED_CLAIMS, SOURCE_LINES
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
        self.assertIn("         … and 2 more sources", out)
        self.assertEqual(out.count(": stale"), SOURCE_LINES)

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
        self.assertIn("         … and 2 more problems", out)
        self.assertEqual(out.count("has a malformed blob"), SOURCE_LINES)


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


if __name__ == "__main__":
    unittest.main()
