"""Unit tests for the pure ranking module. No store, no filesystem: `Index`
is built from plain (id, {field: text}) tuples. Spec:
docs/specs/2026-09-16-claimlock-search-ranking-design.md §3.1-3.4 (amended
2026-09-16: the relevance floor is now absence-based, not document-frequency
based — see the fix-round report in
.superpowers/sdd/2026-09-16-claimlock-search-ranking/task-1-report.md)."""
import unittest

import helpers  # noqa: F401  (sys.path setup for `claimlock`, as every test file does)
from claimlock.rank import FIELDS, STOP_WORDS, WEIGHTS, Index, fold, search, tokens


def doc(cid, **fields):
    """A (claim id, {field: text}) pair with every field defaulting to "" —
    the same shape `Index.__init__` expects, one call site per fixture claim."""
    return (cid, {f: fields.get(f, "") for f in FIELDS})


class Constants(unittest.TestCase):
    """The field weights are fixed by spec §3.3 (R2: never tuned against the
    benchmark). Pinning them here means a silent drift shows up as a failing
    unit test, not just a changed benchmark number."""

    def test_weights_match_spec(self):
        self.assertEqual(WEIGHTS, {"id": 3.0, "head": 2.0, "area": 1.5,
                                    "src": 1.0, "body": 1.0, "ev": 0.5})

    def test_stop_words_match_the_fix_round_list(self):
        # Pinned exactly to the set measured in the fix round (spec §3.4
        # amendment) — a silent edit here would silently change which terms
        # count toward the absence floor's denominator.
        expected = frozenset("""
            a an the is are was were be been being do does did done how what when where why
            which who whom this that these those it its of for to in on at by with from as and or but if then
            than so such can could should would may might will shall i you we they he she use used using get
            got make made support supports happens happen
        """.split())
        self.assertEqual(STOP_WORDS, expected)


class Tokens(unittest.TestCase):
    def test_hyphenated_id_splits_on_hyphens(self):
        # "background-job-calls-are-priced" splits on its four hyphens into
        # five words (background, job, calls, are, priced).
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
    deterministic tie-breaking. No filler documents are needed to hold a
    term under a document-frequency ceiling any more — the floor (§3.4,
    amended) no longer looks at document frequency at all, only at whether
    a query's content terms exist in the corpus vocabulary."""

    def test_ubiquitous_content_term_still_ranks(self):
        # "widget" (not a stop word) appears in all 5 docs. Under the old
        # df<=25% floor this returned NOTHING (regression #1's exact
        # mechanism: a common-but-real term was treated as noise). The
        # amended floor only asks whether "widget" exists in the corpus at
        # all — it does — so it must rank normally instead of being silenced.
        docs = [
            doc(f"widget-claim-{i}", id=f"widget claim {i}", body="a widget sits here")
            for i in range(5)
        ]
        index = Index(docs)
        hits = search(index, "widget")
        self.assertEqual(len(hits), 5)

    def test_rarer_term_scores_higher_than_common_term_on_the_same_doc(self):
        # "widget" appears in 1 of 5 docs (rare, high IDF); "thing" appears
        # in all 5 (common, low but nonzero IDF). Compare their contribution
        # on the one doc that contains both.
        docs = [
            doc("widget-claim", id="widget claim", body="a widget thing exists here"),
            doc("filler-one", id="filler one", body="thing filler"),
            doc("filler-two", id="filler two", body="thing filler"),
            doc("filler-three", id="filler three", body="thing filler"),
            doc("filler-four", id="filler four", body="thing filler"),
        ]
        index = Index(docs)
        [widget_score] = [s for s, cid in search(index, "widget") if cid == "widget-claim"]
        [thing_score] = [s for s, cid in search(index, "thing") if cid == "widget-claim"]
        self.assertGreater(widget_score, thing_score)

    def test_rarer_term_scores_higher_than_a_less_rare_one(self):
        # "consistency" appears in 1 of 10 docs (10%); "totals" in 2 of 10
        # (20%) — both were "discriminating" under the old 25% rule and both
        # are simply present under the new one; the rarer one must still
        # score higher (this is IDF, unaffected by the floor rewrite).
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
        # of another — the id field's 3.0 weight against body's 1.0 must put
        # the id match first. No filler docs needed any more.
        docs = [
            doc("widget", id="widget", body="an unrelated claim about filler text"),
            doc("other-claim", id="other claim", body="widget appears once in this body"),
        ]
        index = Index(docs)
        hits = search(index, "widget")
        self.assertEqual([cid for _, cid in hits], ["widget", "other-claim"])

    def test_equal_scores_order_by_claim_id(self):
        # Two claims with byte-identical indexed content (different ids) —
        # BM25 gives them the same score, so ordering must fall back to id,
        # deterministically, twice.
        docs = [
            doc("zeta-claim", id="zeta claim", body="ledger totals reconcile nightly"),
            doc("alpha-claim", id="alpha claim", body="ledger totals reconcile nightly"),
        ]
        index = Index(docs)
        hits1 = search(index, "ledger totals")
        hits2 = search(index, "ledger totals")
        self.assertEqual([cid for _, cid in hits1], ["alpha-claim", "zeta-claim"])
        self.assertEqual([cid for _, cid in hits2], ["alpha-claim", "zeta-claim"])


class RelevanceFloor(unittest.TestCase):
    """The amended §3.4 floor: drop stop words from the folded query; if at
    least half of the remaining content terms are absent from the corpus
    vocabulary entirely, report no hits. Otherwise rank normally and return
    every claim BM25 scores — no per-document-frequency gate any more."""

    def _five_claims(self):
        return [
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

    def test_all_content_terms_absent_means_no_hits_and_names_them(self):
        # "what", "happens", "when" and "a" are stop words; "payment" is the
        # only content term and it appears nowhere in the corpus, so 1/1
        # (100%) of content terms are absent — well over half.
        index = Index(self._five_claims())
        query = "what happens when a payment"
        self.assertEqual(search(index, query), [])
        terms = fold(tokens(query), index.vocab)
        content_terms = [t for t in terms if t not in STOP_WORDS]
        self.assertEqual(index.unknown(content_terms), ["payment"])

    def test_half_content_terms_absent_means_no_hits(self):
        # "payment" and "fails" are both content terms ("fails" is present
        # in every claim, "payment" in none): exactly 1 of 2 (50%) absent,
        # which the spec's "at least half" wording silences. This is
        # regression #2 from the fix-round brief: "fails" being common and
        # real must NOT be enough to answer a query about payments.
        index = Index(self._five_claims())
        self.assertEqual(search(index, "what happens when a payment fails"), [])

    def test_all_content_terms_present_ranks_normally(self):
        # Every content term ("channel", "undeclared") exists in the corpus,
        # so the floor must not fire even though "channel"/"undeclared" are
        # not rare (this used to fail under the old df<=25% rule too, since
        # a 5-claim corpus makes 25% unreachable by any term — regression #3).
        index = Index(self._five_claims())
        hits = search(index, "what happens when a channel is undeclared")
        self.assertIn("ws-publish-needs-channel", [cid for _, cid in hits])

    def test_stop_words_do_not_dilute_the_absent_fraction(self):
        # Five stop words plus exactly one present content term ("ledger"):
        # if stop words counted toward the denominator this would read as
        # 5/6 absent-or-not and could wrongly silence the query. Excluding
        # them leaves a single content term that IS present -> 0% absent.
        docs = [doc("ledger-claim", id="ledger claim", body="ledger totals reconcile")]
        index = Index(docs)
        query = "how do you use the ledger"  # how/do/you/use/the are stop words
        hits = search(index, query)
        self.assertEqual([cid for _, cid in hits], ["ledger-claim"])

    def test_stop_word_only_query_yields_no_hits(self):
        # No content terms at all -> nothing to gate on and nothing
        # meaningful to search for.
        docs = [doc("some-claim", id="some claim", body="the request is handled here")]
        index = Index(docs)
        self.assertEqual(search(index, "how do you use this"), [])


class RegressionFromTheRealStore(unittest.TestCase):
    """Fixtures shaped after the three measured failures in the fix-round
    brief (docs/specs/2026-09-16-claimlock-search-ranking-design.md §3.4),
    so the old df<=25% rule cannot be silently reintroduced without these
    breaking."""

    def test_common_but_real_terms_still_surface_the_right_claim(self):
        # "grpc" at 39% and "error" at 46% document frequency (both over the
        # old 25% ceiling) must not delete the real answer, and an unrelated
        # claim that only shares the rarer word "handling" must not win.
        docs = [
            doc("grpc-error-details-are-bounded-never-fatal",
                id="grpc error details are bounded never fatal",
                body="a structured grpc error detail is dropped rather than failing the "
                     "whole rpc call; grpc error handling never turns a detail overflow fatal"),
            doc("grpc-status-rides-reserved-headers", id="grpc status rides reserved headers",
                body="grpc status travels on reserved response headers; no forged error can appear there"),
            doc("grpc-reflection-is-host-native", id="grpc reflection is host native",
                body="a grpc reflection query is answered by the host, no guest involved"),
            doc("schema-conflict-blocks-provision", id="schema conflict blocks provision",
                body="a schema error blocks provisioning with a 409 conflict"),
            doc("tx-conflict-is-409", id="tx conflict is 409",
                body="a commit error on a transaction conflict returns 409, no auto retry; "
                     "this has nothing to do with grpc"),
            doc("unrelated-handling-claim", id="generic request handling claim",
                body="this claim is about handling requests in an unrelated subsystem entirely"),
            doc("filler-one", id="filler claim one", body="totally unrelated content here"),
            doc("filler-two", id="filler claim two", body="totally unrelated content here"),
            doc("filler-three", id="filler claim three", body="totally unrelated content here"),
            doc("filler-four", id="filler claim four", body="totally unrelated content here"),
            doc("filler-five", id="filler claim five", body="totally unrelated content here"),
            doc("filler-six", id="filler claim six", body="totally unrelated content here"),
            doc("filler-seven", id="filler claim seven", body="totally unrelated content here"),
        ]
        # sanity: "grpc" and "error" really are common in this fixture, well
        # over the old 25% ceiling, so this is a faithful repro.
        def doc_ratio(term):
            hits = sum(1 for _, f in docs if term in tokens(f["id"] + " " + f["body"]))
            return hits / len(docs)

        self.assertGreater(doc_ratio("grpc"), 0.25)
        self.assertGreater(doc_ratio("error"), 0.25)

        index = Index(docs)
        hits = search(index, "grpc error handling")
        self.assertTrue(hits, "grpc-error query returned nothing — the old df floor is back")
        self.assertEqual(hits[0][1], "grpc-error-details-are-bounded-never-fatal")

    def test_unanswerable_query_over_a_common_word_stays_silent(self):
        # "fails" is present in most claims (a real, common word); "payment"
        # is absent. The right behaviour is silence, not an answer built
        # from "fails" alone.
        index = Index(RelevanceFloor()._five_claims())
        self.assertEqual(search(index, "what happens when a payment fails"), [])

    def test_store_of_three_claims_or_fewer_still_answers(self):
        # The old df<=25% rule made ANY query against a store of <=3 claims
        # return nothing, because every term necessarily exceeds 25% of a
        # 3-document corpus. A brand-new claimlock repo must not look
        # silently broken.
        for n in (1, 2, 3):
            with self.subTest(n=n):
                docs = [doc(f"claim-{i}", id=f"claim {i} about pricing",
                             body="a background job is priced once per invocation")
                        for i in range(n)]
                index = Index(docs)
                hits = search(index, "background job priced")
                self.assertEqual(len(hits), n)


class NNoContentWordsSizes(unittest.TestCase):
    """§3.6: the floor must not depend on corpus size."""

    def test_n1_finds_the_single_claim_with_its_own_words(self):
        index = Index([doc("only-claim", id="only claim about widgets",
                            body="a widget is the only thing here")])
        hits = search(index, "widget")
        self.assertEqual([cid for _, cid in hits], ["only-claim"])

    def test_n2_finds_the_matching_claim(self):
        docs = [
            doc("widget-claim", id="widget claim", body="a widget sits here"),
            doc("gadget-claim", id="gadget claim", body="a gadget sits here"),
        ]
        index = Index(docs)
        hits = search(index, "widget")
        self.assertEqual([cid for _, cid in hits], ["widget-claim"])

    def test_n3_finds_the_matching_claim(self):
        docs = [
            doc("widget-claim", id="widget claim", body="a widget sits here"),
            doc("gadget-claim", id="gadget claim", body="a gadget sits here"),
            doc("gizmo-claim", id="gizmo claim", body="a gizmo sits here"),
        ]
        index = Index(docs)
        hits = search(index, "widget")
        self.assertEqual([cid for _, cid in hits], ["widget-claim"])


class Limit(unittest.TestCase):
    def test_limit_bounds_results_and_none_means_everything(self):
        docs = [doc(f"ledger-claim-{i}", id=f"ledger claim {i}", body="ledger totals reconcile")
                for i in range(4)]
        index = Index(docs)
        all_hits = search(index, "ledger totals", limit=None)
        self.assertEqual(len(all_hits), 4)
        capped = search(index, "ledger totals", limit=2)
        self.assertEqual(len(capped), 2)
        self.assertEqual(capped, all_hits[:2])


if __name__ == "__main__":
    unittest.main()
