"""Task 1: `check` prints a bounded report by default — hints once, capped
claims and sources — and `check --json` carries only blocking (+ owed) claims
unless `--full` is given. Spec: docs/specs/2026-09-15-claimlock-output-budget-design.md §3.1/§3.2.

Task 3 (bottom of this file): `diff` caps unified-diff lines per source.
Spec §3.5."""
import difflib
import json
import shutil
import unittest

from helpers import TmpCase, claim_text, git, make_repo, pinned_text, run_cli, write
from claimlock.cli import (BODY_LINES, DIFF_LINES, EVIDENCE_CHARS, HEADLINE_CHARS, HINT,
                           LISTED_CLAIMS, SOURCE_LINES)
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def _region_claim_text(cid, path, region, status="unverified", area="core"):
    """A claim citing one region source, block-style (matches
    tests/test_regions.py's helper of the same shape — duplicated locally
    rather than cross-imported, per this test suite's convention)."""
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c",
             "sources:", f"  - path: {path}", f"    region: {region}",
             "---", "Holds.", ""]
    return "\n".join(lines)


def _diff_line_count(old_text, new_text):
    """How many unified-diff lines (headers included) `difflib.unified_diff`
    produces for these two texts — independent of the real fromfile/tofile
    strings `cmd_diff` uses, since those never change the line COUNT."""
    old_lines = old_text.split("\n")[:-1] if old_text.endswith("\n") else old_text.split("\n")
    new_lines = new_text.split("\n")[:-1] if new_text.endswith("\n") else new_text.split("\n")
    return len(list(difflib.unified_diff(old_lines, new_lines, fromfile="x", tofile="y", lineterm="")))


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


class InvalidClaimSortsBeforeStaleClaims(CheckStoreMixin, TmpCase):
    """Fix-wave ruling (item D): the failing-claims list sorts claims with
    problems (INVALID) before merely non-fresh ones, preserving order within
    each group, so an invalid claim past LISTED_CLAIMS is never hidden behind
    a run of stale claims. `invalid` has no `HINT` entry (`_hints_block`
    skips it), so before this fix the census `1 invalid` line was the only
    place such a claim showed at all."""

    def test_invalid_claim_past_the_stale_run_still_appears(self):
        root, ids = self.store(22)
        write(root, "src/zzz-invalid.py", "content\n")
        write(root, "claims/zzz-invalid.md",
              "---\nid: zzz-invalid\narea: core\nstatus: unverified\n"
              "evidence:\n  - kind: test\n    ref: s::c\n"
              "sources:\n  - path: src/zzz-invalid.py\n    blob: not-a-valid-blob\n"
              "---\nBody.\n")
        rc, out, err = run_cli(root, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("INVALID  zzz-invalid", out)
        # Sorted first: the invalid claim occupies one of the LISTED_CLAIMS
        # slots, so only LISTED_CLAIMS - 1 of the 22 stale claims are printed.
        self.assertEqual(out.count("STALE    claim-"), LISTED_CLAIMS - 1)
        self.assertIn("… and 3 more failing claims — claimlock check --full", out)


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


@NEED_GIT
class ElsewhereListSortsProblemsFirst(TmpCase):
    """Cleanup item 3 (spec §4): `cmd_check` sorts `blocking` problems-first
    but left `elsewhere` (the "pre-existing (not changed here):" list) in
    claim-id order, so an invalid claim sorting alphabetically last could
    fall past LISTED_CLAIMS and never be named anywhere but the census."""

    def test_invalid_pre_existing_claim_past_the_stale_run_still_appears(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        ids = [f"claim-{i:03d}" for i in range(22)]
        for cid in ids:
            src = f"src/{cid}.py"
            write(root, src, "original\n")
            write(root, f"claims/{cid}.md", claim_text(cid, sources=(src,)))
        run_cli(root, "verify", *ids)
        write(root, "src/zzz-invalid.py", "content\n")
        write(root, "claims/zzz-invalid.md",
              "---\nid: zzz-invalid\narea: core\nstatus: unverified\n"
              "evidence:\n  - kind: test\n    ref: s::c\n"
              "sources:\n  - path: src/zzz-invalid.py\n    blob: not-a-valid-blob\n"
              "---\nBody.\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "base")
        for cid in ids:
            write(root, f"src/{cid}.py", "CHANGED\n")
        git(root, "commit", "-qam", "drift on main")  # pre-existing, never re-verified
        git(root, "checkout", "-q", "-b", "feature")
        write(root, "unrelated.txt", "x\n")
        git(root, "add", "unrelated.txt")
        git(root, "commit", "-qm", "unrelated change")
        rc, out, err = run_cli(root, "check", "--changed", "main")
        self.assertEqual(rc, 0, out + err)  # nothing in scope, so this doesn't block
        self.assertIn("pre-existing (not changed here):", out)
        self.assertIn("  zzz-invalid: invalid", out)
        self.assertEqual(out.count(": stale"), LISTED_CLAIMS - 1)
        self.assertIn("… and 3 more pre-existing claims — claimlock check --full", out)


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
    """Regression guards, not design targets: if a measured default output
    exceeds these while the shape matches the spec, the ceiling is raised to
    the measured value rounded up rather than the format shrunk."""

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
        # Task 2: an unbroken run of 200 "n"s tokenises as ONE 200-char term
        # (rank.tokens splits on non-alphanumerics only), so the single
        # character "n" is no longer itself a term this store's vocabulary
        # contains once search is ranked -- this test is about the headline
        # clip, not tokenisation, so --literal (today's substring match)
        # keeps testing that.
        rc, out, err = run_cli(root, "search", "--literal", "n")
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
        # Task 2: neither "not" nor "there" appears anywhere in this one-claim
        # store's vocabulary, so the relevance floor fires and the message
        # names both absent content terms. Item F: every ranked no-match also
        # names the most common remedy (`--literal`).
        self.assertEqual(out, "claimlock: nothing matches 'not-there' (no claim mentions: not, there)"
                              " — or try --literal for a substring or path\n")

    def test_a_hit_exits_zero(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="something"))
        rc, out, err = run_cli(root, "search", "something")
        self.assertEqual(rc, 0, out + err)


class SearchEmptyBodyNoTrailingSpace(TmpCase):
    """Item F (fix wave): a claim with no headline (empty body) matched by id
    must not leave the two-space header/headline separator dangling at the
    end of the line."""

    def test_no_headline_leaves_no_trailing_whitespace(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/nobody.md", claim_text("nobody", sources=["src/a.py"], body=""))
        rc, out, err = run_cli(root, "search", "nobody")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out, "nobody (core, unverified)\n")


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
    """Regression guard, not a design target: this class's job is to fail if
    the --body suppression regresses, not to hold the format to an exact
    byte count. A single matching line per claim can't show that — deleting
    the whole default/--body distinction would still pass an absolute-only
    ceiling measured against the pre-feature output — so the house form (also
    used by `MixedStoreJsonBudget` and `ListBudgetCeiling`) is a *relational*
    assertion (`default <= full/2`) that the feature must exist to satisfy,
    plus the absolute ceiling as a secondary regression check. Each claim
    here gets several matching body lines so `--body`'s output is actually
    fat enough for the cap to bind."""

    def test_search_matching_30_claims_is_bounded(self):
        root = make_repo(self.tmp / "r", use_git=False)
        for i in range(30):
            cid = f"claim-{i:03d}"
            write(root, f"src/{cid}.py", "x\n")
            body = "\n".join(
                f"needle appears on line {k} of claim {cid}, with some more words to pad it out further."
                for k in range(8))
            write(root, f"claims/{cid}.md", claim_text(cid, sources=[f"src/{cid}.py"], body=body))
        rc, out, err = run_cli(root, "search", "needle")
        self.assertEqual(rc, 0, out + err)
        size = len(out.encode("utf-8"))

        rc, out_body, err = run_cli(root, "search", "needle", "--body")
        self.assertEqual(rc, 0, out_body + err)
        body_size = len(out_body.encode("utf-8"))

        # Measured 2026-09-15: default 3,570 B, --body 23,220 B. Now capped
        # further by --top's default of 10 (Task 2), so both numbers are
        # smaller still; the relational assertion below is what matters.
        self.assertLessEqual(size, body_size / 2,
                              f"default {size} B should be at most half of --body {body_size} B "
                              f"(30 claims, 8 matching body lines each)")
        self.assertLessEqual(size, 4000, f"default `search` matching 30 claims was {size} bytes")


class SearchRankedOrder(TmpCase):
    """Task 2, spec §3.5: ranked order — an id match outranks a deep-body
    match. Query terms otherwise absent from spec §3.4's stop list keep this
    from ever hitting the relevance floor (both claims share the query term)."""

    def test_id_match_outranks_deep_body_match(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "src/b.py", "y\n")
        write(root, "claims/pricing.md", claim_text(
            "pricing", sources=["src/a.py"], body="How the system charges for usage."))
        write(root, "claims/other.md", claim_text(
            "other", sources=["src/b.py"],
            body="Padding text that goes on for a while before, eventually, mentioning pricing once."))
        rc, out, err = run_cli(root, "search", "pricing")
        self.assertEqual(rc, 0, out + err)
        ids = [line.split(" ", 1)[0] for line in out.splitlines() if line and not line.startswith("…")]
        self.assertEqual(ids, ["pricing", "other"])


class SearchTopFlag(TmpCase):
    """Task 2, spec §3.5: `--top N`, default 10; a cut names the exact
    command to see the rest. Every claim here shares an identical body
    (only in the `body` field, never the id), so ranking ties are broken
    deterministically by claim id (rank.py) and the printed order is
    exactly sorted-by-id."""

    def _store_of(self, n):
        root = make_repo(self.tmp / "r", use_git=False)
        for i in range(n):
            cid = f"item-{i:02d}"
            write(root, f"src/{cid}.py", "x\n")
            write(root, f"claims/{cid}.md", claim_text(cid, sources=[f"src/{cid}.py"], body="widget appears here"))
        return root

    def test_default_caps_at_10_with_exact_cut_note(self):
        root = self._store_of(15)
        rc, out, err = run_cli(root, "search", "widget")
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        self.assertEqual(len(lines), 11, out)
        self.assertEqual(lines[-1], "… and 5 more — claimlock search 'widget' --top 15")
        ids = [line.split(" ", 1)[0] for line in lines[:-1]]
        self.assertEqual(ids, [f"item-{i:02d}" for i in range(10)])

    def test_explicit_top_widens_the_cap(self):
        root = self._store_of(15)
        rc, out, err = run_cli(root, "search", "widget", "--top", "15")
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        self.assertEqual(len(lines), 15, out)

    def test_top_zero_is_refused_with_exit_2(self):
        root = self._store_of(1)
        rc, out, err = run_cli(root, "search", "widget", "--top", "0")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("--top", err)

    def test_top_negative_is_refused_with_exit_2(self):
        root = self._store_of(1)
        rc, out, err = run_cli(root, "search", "widget", "--top=-3")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("--top", err)


class SearchQueryWithBraceIsNeverAFormatString(TmpCase):
    """Item A (2026-09-16 fix wave): the cut note used to build `more` as an
    f-string carrying the raw query, then hand it to `_capped`, which calls
    `.format(n=...)` on it -- so a query containing `{widget}`, `{n}`, `{}`
    or `{0}` collided with that call. A brace in the query must never crash
    the command (the hits are computed and worth printing) or corrupt the
    printed note; the fix builds the note after the cut, never through
    `_capped`'s `.format`."""

    def _store_of(self, n):
        root = make_repo(self.tmp / "r", use_git=False)
        for i in range(n):
            cid = f"item-{i:02d}"
            write(root, f"src/{cid}.py", "x\n")
            write(root, f"claims/{cid}.md",
                  claim_text(cid, sources=[f"src/{cid}.py"], body="widget appears here"))
        return root

    def _assert_cut_note_intact(self, query):
        root = self._store_of(15)
        rc, out, err = run_cli(root, "search", query)
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        self.assertEqual(len(lines), 11, out)
        self.assertEqual(lines[-1], f"… and 5 more — claimlock search {query!r} --top 15", out)
        ids = [line.split(" ", 1)[0] for line in lines[:-1]]
        self.assertEqual(ids, [f"item-{i:02d}" for i in range(10)])

    def test_named_field_key_does_not_crash(self):
        self._assert_cut_note_intact("widget appears {widget}")

    def test_n_field_key_does_not_silently_corrupt_the_note(self):
        # `{n}` happens to match `_capped`'s own placeholder name, so under
        # the bug it did not raise -- it silently replaced the user's `{n}`
        # with the cut count too, corrupting the printed command. Assert the
        # query is echoed back verbatim.
        self._assert_cut_note_intact("widget appears {n}")

    def test_empty_braces_do_not_crash(self):
        self._assert_cut_note_intact("widget appears {}")

    def test_positional_index_does_not_crash(self):
        self._assert_cut_note_intact("widget appears {0}")


class SearchLiteralFlag(TmpCase):
    """Task 2, spec §3.5: `--literal` restores today's exact-substring
    matching, for paths and exact strings."""

    def test_literal_finds_a_path_that_ranking_never_needs(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/limit.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/limit.py"], body="Some claim body."))
        rc, out, err = run_cli(root, "search", "--literal", "src/limit.py")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("c (core, unverified)", out)

    def test_literal_returns_the_old_zero_hits_for_a_query_ranking_answers(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/background-job-calls-are-priced.md", claim_text(
            "background-job-calls-are-priced", sources=["src/a.py"],
            body="A background job is billed once when it is first attempted."))
        query = "how are background jobs priced"
        rc, out, err = run_cli(root, "search", query)
        self.assertEqual(rc, 0, out + err)
        rc, out, err = run_cli(root, "search", "--literal", query)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("nothing matches", out)

    def test_top_is_not_validated_under_literal(self):
        # Item H: --top bounds ranked output only; --literal is uncapped by
        # design, so an otherwise-invalid --top (0, negative) is skipped
        # rather than refused -- --literal --top 0 used to exit 2 for a flag
        # this mode ignores outright.
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="widget appears here"))
        rc, out, err = run_cli(root, "search", "--literal", "widget", "--top", "0")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("c (core, unverified)", out)


class SearchNoMatchNamesAbsentTerms(TmpCase):
    """Task 2, spec §3.4/§3.5: the no-match message names the query's
    content terms absent from the store's vocabulary."""

    def test_message_names_absent_terms(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="Refunds happen automatically."))
        rc, out, err = run_cli(root, "search", "what happens when a payment fails")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(out, "claimlock: nothing matches 'what happens when a payment fails' "
                              "(no claim mentions: payment, fails)"
                              " — or try --literal for a substring or path\n")

    def test_stop_word_only_query_omits_the_absent_terms_clause(self):
        # Every term here is a stop word, so there are no content terms to
        # report as absent -- that clause is omitted rather than printed
        # empty, but item F's --literal remedy is still appended.
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], body="Something unrelated."))
        rc, out, err = run_cli(root, "search", "how do you use this")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(out, "claimlock: nothing matches 'how do you use this'"
                              " — or try --literal for a substring or path\n")


class SearchBodyUnderRanking(TmpCase):
    """Task 2 / item D (2026-09-16 fix wave): `--body` still prints matching
    body lines, now under ranked hits rather than substring-matched ones.
    Item D: `_print_search_hit` used to keep the OLD whole-query substring
    test even under ranking, so a question-shaped query -- never a literal
    substring of any body line -- printed a header and a blank line and
    nothing else. Fixed by matching per-line on the query's folded content
    terms (`rank.fold`/`rank.tokens`/`rank.STOP_WORDS`), not the raw query
    string."""

    def test_body_flag_prints_matching_lines_under_the_ranked_hit(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/pricing.md", claim_text(
            "pricing", sources=["src/a.py"],
            body="pricing line one\nunrelated line\npricing line two"))
        rc, out, err = run_cli(root, "search", "pricing", "--body")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out, "pricing (core, unverified)\n    pricing line one\n    pricing line two\n\n")

    def test_question_shaped_query_still_prints_matching_lines(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/background-job-charge.md", claim_text(
            "background-job-charge", sources=["src/a.py"],
            body="A background job is billed once when it is first attempted.\n"
                 "Unrelated line here."))
        # Never a literal substring of the body: the old code's raw
        # whole-query substring test matched nothing here, printing only the
        # header and a blank line. "jobs" (plural) also exercises the same
        # corpus-aware folding `rank.search` itself uses -- the body only
        # has "job" (singular).
        rc, out, err = run_cli(root, "search", "how are background jobs charged", "--body")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(
            out,
            "background-job-charge (core, unverified)\n"
            "    A background job is billed once when it is first attempted.\n\n")


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
        # The exact shape `show c --full` prints for this (unpinned,
        # unverified) claim: status line, blank, the whole body, blank,
        # Evidence: with the whole ref, Sources: with the source's unpinned
        # state, blank, file:. `body` and `long_ref` come from the fixture
        # itself; the surrounding template is what this test pins — the
        # guarantee that `--full` restores everything byte-for-byte only
        # holds if a regression here fails the test, not merely if a
        # substring goes missing.
        expected = (
            "c (core) — unverified\n"
            "\n"
            f"{body}\n"
            "\n"
            "Evidence:\n"
            f"  [test] {long_ref}\n"
            "Sources (a change here makes this claim stale):\n"
            "  src/a.py — - (unpinned)\n"
            "\n"
            "file: claims/c.md\n"
        )
        self.assertEqual(out, expected)


class ShowBudgetCeiling(TmpCase):
    """Regression guard, not a design target: a 60-line body and three
    500-char refs is barely over BODY_LINES/EVIDENCE_CHARS, so an absolute
    ceiling measured against that fixture passed against the pre-branch
    (uncapped) output too and guarded nothing. The house form (also used by
    `MixedStoreJsonBudget`, `ListBudgetCeiling` and `SearchBudgetCeiling`) is
    a *relational* assertion (`default <= full/2`) the feature must exist to
    satisfy, plus the absolute ceiling as a secondary check — so the fixture
    here is fattened well past both caps until the relation actually binds."""

    def test_long_body_and_refs_is_bounded(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        body = "\n".join(
            f"line {k} of the body, with some more words to pad it out further." for k in range(300))
        evidence = tuple(("test", "r" * 2000) for _ in range(3))
        write(root, "claims/c.md", claim_text("c", sources=["src/a.py"], evidence=evidence, body=body))
        rc, out, err = run_cli(root, "show", "c")
        self.assertEqual(rc, 0, out + err)
        size = len(out.encode("utf-8"))

        rc, out_full, err = run_cli(root, "show", "c", "--full")
        self.assertEqual(rc, 0, out_full + err)
        full_size = len(out_full.encode("utf-8"))

        # Measured 2026-09-15: default 3,397 B, --full 25,851 B.
        self.assertLessEqual(size, full_size / 2,
                              f"default {size} B should be at most half of --full {full_size} B "
                              f"(300-line body, three 2000-char refs)")
        self.assertLessEqual(size, 4000,
                              f"`show` with a 300-line body and three 2000-char refs was {size} bytes")


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
        # `long_line` is the fixture's own value; the surrounding template
        # (mark, id, area, indent) is what this test pins, so a regression in
        # the un-clipped path fails the test rather than merely disappearing
        # from a substring check.
        expected = f"? c (core)\n    {long_line}\n"
        self.assertEqual(out, expected)


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


class ListBudgetCeiling(TmpCase):
    """Regression guard, not a design target (ceilings track measured
    output, not a byte budget the format is held to). Spec §6 asks for a
    per-command output-budget test; `list`'s only got exercised for exact
    shape above, not size, until now.

    The headline here is made much longer than HEADLINE_CHARS on purpose: a
    realistic ~200-char headline (this class's sibling `ListHeadlineTruncated`
    fixture) is still clipped to 120 either way, but the per-claim header line
    (`? claim-000 (core)`) is fixed overhead that keeps default well over half
    of --full unless the headline itself dominates the line — so a fixture
    that actually demonstrates the clip saving needs a headline long enough
    for that overhead to be negligible by comparison."""

    def test_30_claims_with_long_headlines_is_bounded_and_half_of_full(self):
        root = make_repo(self.tmp / "r", use_git=False)
        long_line = "h" * 2000
        for i in range(30):
            cid = f"claim-{i:03d}"
            write(root, f"src/{cid}.py", "x\n")
            write(root, f"claims/{cid}.md", claim_text(cid, sources=[f"src/{cid}.py"], body=long_line))

        rc, out, err = run_cli(root, "list")
        self.assertEqual(rc, 0, out + err)
        size = len(out.encode("utf-8"))
        self.assertLessEqual(size, 5000,
                              f"default `list` over 30 claims with 2000-char headlines was {size} bytes")

        rc, out_full, err = run_cli(root, "list", "--full")
        self.assertEqual(rc, 0, out_full + err)
        full_size = len(out_full.encode("utf-8"))
        self.assertLessEqual(size, full_size / 2,
                              f"default {size} B should be at most half of --full {full_size} B (30 claims)")


class RewrittenFileMixin:
    """A claim pinning one 900-line file, verified and committed, then the
    whole file rewritten (every line differs) — the worst-case `diff` shape
    from spec §1 (measured there at ~44,000 B on a 2,400-line file)."""

    OLD_TEXT = "\n".join(f"line {k} of the original file." for k in range(900)) + "\n"
    NEW_TEXT = "\n".join(f"line {k} REWRITTEN." for k in range(900)) + "\n"

    def rewritten_file_claim(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "big.py", self.OLD_TEXT)
        write(root, "claims/c.md", claim_text("c", sources=["big.py"], body="Holds."))
        rc, out, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        write(root, "big.py", self.NEW_TEXT)
        return root

    def pinned_blob(self, root):
        text = (root / "claims/c.md").read_text()
        line = next(l for l in text.splitlines() if l.strip().startswith("blob:"))
        return line.split("blob:", 1)[1].strip()


@NEED_GIT
class DiffCapOnRewrittenFile(RewrittenFileMixin, TmpCase):
    def setUp(self):
        super().setUp()
        self.total = _diff_line_count(self.OLD_TEXT, self.NEW_TEXT)
        self.assertGreater(self.total, DIFF_LINES, "fixture must exceed the cap to test it")

    def test_default_caps_at_DIFF_LINES_then_a_note_naming_the_withheld_count(self):
        root = self.rewritten_file_claim()
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        out_lines = out.rstrip("\n").split("\n")
        self.assertEqual(len(out_lines), DIFF_LINES + 1)
        left = self.total - DIFF_LINES
        self.assertEqual(out_lines[-1], f"… and {left} more diff lines — claimlock diff c --full")

    def test_capped_lines_are_a_prefix_of_the_full_diff(self):
        root = self.rewritten_file_claim()
        blob = self.pinned_blob(root)
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        full = list(difflib.unified_diff(
            self.OLD_TEXT.split("\n")[:-1], self.NEW_TEXT.split("\n")[:-1],
            fromfile=f"big.py @ {blob[:12]} (verified)", tofile="big.py (now)", lineterm=""))
        out_lines = out.rstrip("\n").split("\n")
        self.assertEqual(out_lines[:DIFF_LINES], full[:DIFF_LINES])


@NEED_GIT
class DiffFullFlagRestoresCompleteDiff(RewrittenFileMixin, TmpCase):
    def test_full_prints_the_complete_unified_diff_byte_for_byte(self):
        root = self.rewritten_file_claim()
        blob = self.pinned_blob(root)
        rc, out, err = run_cli(root, "diff", "c", "--full")
        self.assertEqual(rc, 0, out + err)
        expected = "\n".join(difflib.unified_diff(
            self.OLD_TEXT.split("\n")[:-1], self.NEW_TEXT.split("\n")[:-1],
            fromfile=f"big.py @ {blob[:12]} (verified)", tofile="big.py (now)", lineterm="")) + "\n"
        self.assertEqual(out, expected)
        self.assertNotIn("more diff lines", out)


@NEED_GIT
class DiffSmallChangeUncapped(TmpCase):
    def test_one_line_change_prints_exactly_as_full_with_no_cap_note(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\ntwo\nthree\n")
        write(root, "claims/c.md", claim_text("c", sources=["a.py"], body="Holds."))
        rc, out, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        write(root, "a.py", "one\nTWO\nthree\n")
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("more diff lines", out)
        self.assertIn("-two", out)
        self.assertIn("+TWO", out)
        rc, out_full, err = run_cli(root, "diff", "c", "--full")
        self.assertEqual(rc, 0, out_full + err)
        self.assertEqual(out, out_full)


@NEED_GIT
class DiffCapsEachSourceIndependently(TmpCase):
    def test_a_large_source_is_capped_while_a_small_one_prints_in_full(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        old_big = "\n".join(f"line {k}" for k in range(900)) + "\n"
        new_big = "\n".join(f"line {k} CHANGED" for k in range(900)) + "\n"
        write(root, "big.py", old_big)
        write(root, "small.py", "one\ntwo\nthree\n")
        write(root, "claims/c.md", claim_text("c", sources=["big.py", "small.py"], body="Holds."))
        rc, out, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        write(root, "big.py", new_big)
        write(root, "small.py", "one\nTWO\nthree\n")
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        total_big = _diff_line_count(old_big, new_big)
        self.assertGreater(total_big, DIFF_LINES, "fixture must exceed the cap to test it")
        left = total_big - DIFF_LINES
        note = f"… and {left} more diff lines — claimlock diff c --full"
        self.assertEqual(out.count("more diff lines"), 1, "only the large source should be capped")
        self.assertIn(note, out)
        self.assertIn("+TWO", out, "the small source's diff still prints in full")
        self.assertLess(out.index(note), out.index("small.py"),
                         "the big source's cap note comes before the small source's diff block")


@NEED_GIT
class DiffRegionSourceCap(TmpCase):
    def test_region_diff_obeys_the_same_cap(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        region_old = "\n".join(f"r{k}" for k in range(900))
        region_new = "\n".join(f"r{k} CHANGED" for k in range(900))
        old_text = f"before\n# claimlock:begin r1\n{region_old}\n# claimlock:end r1\nafter\n"
        new_text = f"before\n# claimlock:begin r1\n{region_new}\n# claimlock:end r1\nafter\n"
        write(root, "a.py", old_text)
        write(root, "claims/c.md", _region_claim_text("c", "a.py", "r1"))
        rc, out, err = run_cli(root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        write(root, "a.py", new_text)
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        total = len(list(difflib.unified_diff(
            region_old.split("\n"), region_new.split("\n"), fromfile="x", tofile="y", lineterm="")))
        self.assertGreater(total, DIFF_LINES, "fixture must exceed the cap to test it")
        left = total - DIFF_LINES
        out_lines = out.rstrip("\n").split("\n")
        self.assertEqual(len(out_lines), DIFF_LINES + 1)
        self.assertEqual(out_lines[-1], f"… and {left} more diff lines — claimlock diff c --full")


@NEED_GIT
class DiffBudgetCeiling(RewrittenFileMixin, TmpCase):
    """Regression guard, not a design target — the DIFF_LINES cap itself is
    the design target; this only checks the cap keeps a large diff bounded.
    Spec §1 measured today's unbounded output at ~44,000 B on a 2,400-line
    rewritten file; the brief's ceiling for this 900-line fixture is
    12,000 B."""

    def test_rewritten_900_line_file_default_diff_is_bounded(self):
        root = self.rewritten_file_claim()
        rc, out, err = run_cli(root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        size = len(out.encode("utf-8"))
        self.assertLessEqual(size, 12000, f"default `diff` on a rewritten 900-line file was {size} bytes")


if __name__ == "__main__":
    unittest.main()
