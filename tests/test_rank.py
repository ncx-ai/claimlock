"""Unit tests for the pure ranking module. No store, no filesystem: `Index`
is built from plain (id, {field: text}) tuples. Spec:
docs/specs/2026-09-16-claimlock-search-ranking-design.md §3.1-3.4."""
import unittest

import helpers  # noqa: F401  (sys.path setup for `claimlock`, as every test file does)
from claimlock.rank import DISCRIMINATING, FIELDS, WEIGHTS, Index, fold, search, tokens


def doc(cid, **fields):
    """A (claim id, {field: text}) pair with every field defaulting to "" —
    the same shape `Index.__init__` expects, one call site per fixture claim."""
    return (cid, {f: fields.get(f, "") for f in FIELDS})


class Constants(unittest.TestCase):
    """The field weights and discriminating threshold are fixed by spec
    §3.3/§3.4 (R2: never tuned against the benchmark). Pinning them here
    means a silent drift shows up as a failing unit test, not just a
    changed benchmark number."""

    def test_weights_match_spec(self):
        self.assertEqual(WEIGHTS, {"id": 3.0, "head": 2.0, "area": 1.5,
                                    "src": 1.0, "body": 1.0, "ev": 0.5})

    def test_discriminating_threshold_is_one_quarter(self):
        self.assertEqual(DISCRIMINATING, 0.25)


class Tokens(unittest.TestCase):
    def test_hyphenated_id_splits_on_hyphens(self):
        # "background-job-calls-are-priced" splits on its four hyphens into
        # five words (background, job, calls, are, priced) — not six as an
        # earlier draft of the spec/plan claimed; see the task report.
        self.assertEqual(
            tokens("background-job-calls-are-priced"),
            ["background", "job", "calls", "are", "priced"],
        )

    def test_punctuation_mixed_case_and_path_all_split_on_non_alphanumerics(self):
        self.assertEqual(tokens("Is the Ledger Consistent?"), ["is", "the", "ledger", "consistent"])
        self.assertEqual(tokens("src/limit.py"), ["src", "limit", "py"])

    def test_empty_string_is_empty_list(self):
        self.assertEqual(tokens(""), [])


class Fold(unittest.TestCase):
    def test_s_suffix_folds_only_when_stem_in_vocab(self):
        vocab = {"job", "price"}
        self.assertEqual(fold(["jobs"], vocab), ["job"])
        self.assertEqual(fold(["jobs"], {"price"}), ["jobs"])

    def test_es_suffix_folds_only_when_stem_in_vocab(self):
        vocab = {"box"}
        self.assertEqual(fold(["boxes"], vocab), ["box"])
        self.assertEqual(fold(["boxes"], {"price"}), ["boxes"])

    def test_ed_suffix_folds_only_when_stem_in_vocab(self):
        vocab = {"match"}
        self.assertEqual(fold(["matched"], vocab), ["match"])
        self.assertEqual(fold(["matched"], {"price"}), ["matched"])

    def test_ing_to_e_folds_only_when_stem_in_vocab(self):
        vocab = {"price"}
        self.assertEqual(fold(["pricing"], vocab), ["price"])
        self.assertEqual(fold(["pricing"], {"job"}), ["pricing"])

    def test_bare_ing_folds_only_when_stem_in_vocab(self):
        vocab = {"mean"}
        self.assertEqual(fold(["meaning"], vocab), ["mean"])
        self.assertEqual(fold(["meaning"], {"price"}), ["meaning"])

    def test_term_already_in_vocab_is_never_folded(self):
        # "jobs" is itself a corpus term here, so it must not fold to "job"
        # even though "job" is also present.
        self.assertEqual(fold(["jobs"], {"jobs", "job"}), ["jobs"])

    def test_order_tries_s_before_es(self):
        # "boxes" ends in both "s" (-> "boxe") and "es" (-> "box"). Only the
        # -es candidate is a real word here, proving -es is tried and not
        # short-circuited by a wrong -s hit.
        self.assertEqual(fold(["boxes"], {"box"}), ["box"])


class RankingBehaviour(unittest.TestCase):
    """Corpus-level behaviour of Index/search: IDF, field weights, and
    deterministic tie-breaking."""

    def test_ubiquitous_term_alone_yields_no_hit(self):
        # "the" appears in every claim's body; a query of only that term
        # must not surface anything (IDF collapses it, and it can never be
        # discriminating since its doc frequency is 100% of the corpus).
        docs = [
            doc("alpha-claim", id="alpha claim", body="the alpha thing is true"),
            doc("beta-claim", id="beta claim", body="the beta thing is true"),
            doc("gamma-claim", id="gamma claim", body="the gamma thing is true"),
            doc("delta-claim", id="delta claim", body="the delta thing is true"),
            doc("epsilon-claim", id="epsilon claim", body="the epsilon thing is true"),
        ]
        index = Index(docs)
        self.assertEqual(search(index, "the"), [])

    def test_rarer_term_outranks_common_term(self):
        # "widget" appears in 1 of 5 docs (rare, high IDF); "thing" appears
        # in all 5 (common, ~zero IDF). Both are matched by their own doc's
        # body only, so the rare-term doc must score higher.
        docs = [
            doc("widget-claim", id="widget claim", body="a widget exists here"),
            doc("thing-claim", id="thing claim", body="a thing exists here too"),
            doc("filler-one", id="filler one", body="thing thing thing filler"),
            doc("filler-two", id="filler two", body="thing thing thing filler"),
            doc("filler-three", id="filler three", body="thing thing thing filler"),
        ]
        index = Index(docs)
        widget_hits = dict((cid, score) for score, cid in search(index, "widget"))
        thing_hits = dict((cid, score) for score, cid in search(index, "thing"))
        self.assertIn("widget-claim", widget_hits)
        # "thing" is in every doc (doc freq 5/5 > 25%), so it is never
        # discriminating and must yield no hits at all.
        self.assertEqual(thing_hits, {})

    def test_rarer_discriminating_term_scores_higher_than_a_less_rare_one(self):
        # "consistency" appears in 1 of 10 docs (10%); "totals" in 2 of 10
        # (20%) — both under the 25% discriminating floor, so both qualify,
        # and the rarer one must score higher.
        docs = [
            doc("rare-claim", id="rare claim", body="ledger consistency holds"),
            doc("less-rare-claim", id="less rare claim", body="ledger totals ok"),
            doc("totals-again", id="totals again", body="ledger totals again"),
        ]
        docs += [doc(f"filler-{i}", id=f"filler {i}", body="unrelated filler text here")
                 for i in range(7)]
        index = Index(docs)
        [rare_score] = [s for s, cid in search(index, "consistency") if cid == "rare-claim"]
        [less_rare_score] = [s for s, cid in search(index, "totals") if cid == "less-rare-claim"]
        self.assertGreater(rare_score, less_rare_score)

    def test_id_match_outranks_same_match_in_body(self):
        # "widget" is the whole id of one claim and appears once in the body
        # (among filler words) of another, same-length-ish claim — the id
        # field's 3.0 weight against body's 1.0 must put the id match first.
        # 6 filler docs keep "widget"'s doc frequency at the 25% floor
        # (2 of 8 docs) so it stays discriminating.
        docs = [
            doc("widget", id="widget", body="an unrelated claim about filler text"),
            doc("other-claim", id="other claim", body="widget appears once in this body"),
        ] + [doc(f"filler-{i}", id=f"filler claim {i}", body="totally unrelated filler content here")
             for i in range(6)]
        index = Index(docs)
        hits = search(index, "widget")
        self.assertEqual([cid for _, cid in hits], ["widget", "other-claim"])

    def test_equal_scores_order_by_claim_id(self):
        # Two claims with byte-identical indexed content (different ids) —
        # BM25 gives them the same score, so ordering must fall back to id,
        # deterministically, twice. 6 filler docs keep "ledger"/"totals" at
        # the 25% floor (2 of 8 docs).
        docs = [
            doc("zeta-claim", id="zeta claim", body="ledger totals reconcile nightly"),
            doc("alpha-claim", id="alpha claim", body="ledger totals reconcile nightly"),
        ] + [doc(f"filler-{i}", id=f"filler claim {i}", body="totally unrelated filler content here")
             for i in range(6)]
        index = Index(docs)
        hits1 = search(index, "ledger totals")
        hits2 = search(index, "ledger totals")
        self.assertEqual([cid for _, cid in hits1], ["alpha-claim", "zeta-claim"])
        self.assertEqual([cid for _, cid in hits2], ["alpha-claim", "zeta-claim"])


class RelevanceFloor(unittest.TestCase):
    def test_no_discriminating_term_means_no_hits_and_names_the_unknown_term(self):
        # "what", "happens", "when", "a" and "fails" appear in every claim
        # below (df 5/5, well over the 25% floor); "payment" appears in none.
        docs = [
            doc("ws-publish-needs-channel", id="ws publish needs a declared channel",
                body="what happens when a channel is undeclared: publishing fails with an error"),
            doc("retry-fails-when-budget-exceeded", id="retry fails when budget exceeded",
                body="what happens when a budget is exceeded: the retry fails"),
            doc("commit-fails-when-conflict", id="commit fails when conflict",
                body="what happens when a write conflicts: the commit fails"),
            doc("deploy-fails-when-schema-conflicts", id="deploy fails when schema conflicts",
                body="what happens when a schema conflicts: the deploy fails"),
            doc("job-fails-when-retries-exhausted", id="job fails when retries exhausted",
                body="what happens when a retry budget is exhausted: the job fails"),
        ]
        index = Index(docs)
        query = "what happens when a payment fails"
        self.assertEqual(search(index, query), [])
        terms = fold(tokens(query), index.vocab)
        self.assertEqual(index.unknown(terms), ["payment"])

    def test_floor_holds_when_every_matched_term_is_ubiquitous(self):
        # A corpus where the query's only matches are terms present in every
        # claim: no discriminating term exists at all, so there must be no
        # hit even though every claim technically "matches".
        docs = [doc(f"claim-{i}", id=f"claim {i}", body="the request is handled here")
                for i in range(6)]
        index = Index(docs)
        self.assertEqual(search(index, "the request is handled"), [])


class Limit(unittest.TestCase):
    def test_limit_bounds_results_and_none_means_everything(self):
        # 4 matching docs + 12 filler keeps "ledger"/"totals" at exactly the
        # 25% discriminating floor (4 of 16 docs).
        docs = [doc(f"ledger-claim-{i}", id=f"ledger claim {i}", body="ledger totals reconcile")
                for i in range(4)]
        docs += [doc(f"filler-{i}", id=f"filler claim {i}", body="totally unrelated filler content here")
                 for i in range(12)]
        index = Index(docs)
        all_hits = search(index, "ledger totals", limit=None)
        self.assertEqual(len(all_hits), 4)
        capped = search(index, "ledger totals", limit=2)
        self.assertEqual(len(capped), 2)
        self.assertEqual(capped, all_hits[:2])


if __name__ == "__main__":
    unittest.main()
