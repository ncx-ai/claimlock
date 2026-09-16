"""The ranking benchmark (spec §4) and the cost budget (spec §5).

A fixture store of 20 realistic claims — phrase-like ids, one fact each,
several sharing a topic (ledger, schema, websockets, auth each have two) so
ranking has to discriminate rather than pick the only candidate. Modeled on
the real store measured in spec §1.

This is a floor, not a tuning target: field weights are fixed by spec §3.3
(R2) and are never adjusted here to chase a better number. If the floor is
ever missed, the fix is better queries/fixtures or a reported weight
critique — never silently retuning WEIGHTS.
"""
import random
import time
import unittest

import helpers  # noqa: F401  (sys.path setup for `claimlock`, as every test file does)
from claimlock.rank import Index, search

RANK1_FLOOR = 0.80
TOP3_FLOOR = 1.00
COST_BUDGET_SECONDS = 0.050


def claim(cid, head, body, area, src, ev):
    """One fixture claim as an `Index` doc: `(id, {field: text})`."""
    return (cid, {"id": cid.replace("-", " "), "head": head, "area": area,
                  "src": src, "body": body, "ev": ev})


# 20 claims, phrase-like ids, one fact each. Ledger, schema, websockets and
# auth each carry two claims so a query about "ledger" or "schema" alone
# can't win by elimination — the discriminating term has to come from the
# query's own wording.
CLAIMS = [
    claim(
        "background-job-calls-are-priced",
        "Background job calls are priced per invocation, not per retry.",
        "A background job enqueued through jobs_enqueue is billed once when it is "
        "first attempted; retries triggered by a task failure are not separately priced.",
        "pricing", "crates/boogy-scheduler/src/jobs.rs", "test:pricing_job_charge_once",
    ),
    claim(
        "ledger-conflict-returns-409",
        "A ledger commit conflict returns HTTP 409, never a silent retry.",
        "When two transactions touch the same ledger row, the store reports a commit "
        "conflict and the host surfaces it to the caller as 409; there is no automatic retry.",
        "ledger", "crates/boogy-host/src/pricing/ledger.rs", "test:ledger_conflict_409",
    ),
    claim(
        "ledger-reservations-expire-after-lease",
        "A ledger reservation expires when its lease is not settled in time.",
        "A reserved amount that is never settled or released reverts to the account "
        "after the lease window passes, so a crashed request cannot hold funds forever.",
        "ledger", "crates/boogy-host/src/pricing/invoke.rs", "test:lease_expiry_releases_reservation",
    ),
    claim(
        "ws-publish-needs-a-declared-channel",
        "A websocket publish fails unless the channel is declared in the manifest.",
        "Calling the websockets capability's publish function for a channel the manifest "
        "does not declare returns an error before anything is sent.",
        "websockets", "crates/boogy-host/src/ws/publish.rs", "test:ws_publish_undeclared_channel_rejected",
    ),
    claim(
        "schema-conflict-blocks-provision",
        "A declared schema that conflicts with the stored one blocks provisioning with 409.",
        "When a deployment's declared model cannot be reconciled with the columns already "
        "on disk, provisioning is refused with a 409 and the previous deployment is restored "
        "where possible.",
        "schema", "crates/boogy-host/src/schema_apply.rs", "test:schema_conflict_refused",
    ),
    claim(
        "dropped-column-bytes-stay-until-reclaimed",
        "A soft-dropped column's bytes stay in storage until the sweeper reclaims them.",
        "Dropping a column only stops it from being read or required; its bytes remain on "
        "every row until the background reclamation sweeper or the owner's purge endpoint clears them.",
        "schema", "crates/boogy-host/src/bg.rs", "test:dropped_column_bytes_retained",
    ),
    claim(
        "auth-tokens-are-paseto-not-jwt",
        "Boogy issues PASETO v4 tokens, not JWT.",
        "Every minted access token is a PASETO v4 public token signed with an Ed25519 key; "
        "there is no JWT anywhere on the auth path.",
        "auth", "crates/boogy-auth/src/paseto.rs", "test:token_format_is_paseto",
    ),
    claim(
        "api-keys-are-hashed-not-stored",
        "An API key is hashed before it is stored; the raw key is shown once.",
        "sk_ prefixed API keys are format-checked and hashed at creation time; only the "
        "hash is persisted, so a leaked database never reveals a usable key.",
        "auth", "crates/boogy-auth-core/src/api_key.rs", "test:api_key_never_stored_raw",
    ),
    claim(
        "rate-limit-buckets-by-caller-not-service",
        "The ingress rate limiter buckets by caller, not by service.",
        "Two different callers of the same service each get their own token bucket; "
        "there is no shared per-service ceiling on the rate-limit path.",
        "ingress", "crates/boogy-ingress/src/rate_limit.rs", "test:rate_limit_keyed_by_caller",
    ),
    claim(
        "tx-commit-conflict-is-409-no-auto-retry",
        "A transaction commit conflict is a 409; the client must retry the whole request.",
        "When a cross-service transaction's commit fails on a version conflict, the host "
        "does not retry it automatically — the caller sees 409 and resubmits.",
        "transactions", "crates/boogy-host/src/tx.rs", "test:tx_conflict_no_auto_retry",
    ),
    claim(
        "peer-fetch-strips-identity-headers",
        "peer::fetch strips Authorization and Cookie headers on every hop.",
        "A cross-service call through peer::fetch removes Authorization, Cookie, "
        "X-Boogy-Caller and X-Boogy-Workload before the callee ever sees the request.",
        "mesh", "crates/boogy-host/src/peer.rs", "test:peer_fetch_strips_headers",
    ),
    claim(
        "store-scan-caps-at-five-million-rows",
        "An unindexed store scan is capped at 5,000,000 rows.",
        "BOOGY_STORE_MAX_SCAN_ROWS bounds how many rows a single unindexed scan can "
        "accumulate in host memory before the query is refused.",
        "store", "crates/boogy-host/src/db/fdb.rs", "test:scan_row_cap_enforced",
    ),
    claim(
        "cpu-deadline-traps-a-spinning-guest",
        "A guest that spins past its CPU deadline is trapped, not merely slowed.",
        "The epoch deadline traps a CPU-bound guest once it exceeds cpu_deadline_ms; "
        "the request fails rather than running forever.",
        "scheduling", "crates/boogy-scheduler/src/deadline.rs", "test:cpu_deadline_traps_guest",
    ),
    claim(
        "reclamation-sweeper-locks-one-service-at-a-time",
        "The reclamation sweeper takes a per-service advisory lock before sweeping.",
        "Only one host at a time can sweep a given service's dropped columns; the lock "
        "is acquired via an advisory lock keyed on the service id.",
        "schema", "crates/boogy-host/src/bg.rs", "test:sweeper_advisory_lock_per_service",
    ),
    claim(
        "websocket-grant-expiry-releases-membership-not-connection",
        "A websocket subscription grant expiring drops the room, not the socket.",
        "When a private channel's bearer grant's TTL passes, the subscriber's membership "
        "in that channel is dropped while the socket keeps any other rooms it holds.",
        "websockets", "crates/boogy-host/src/ws/grants.rs", "test:grant_expiry_drops_membership_only",
    ),
    claim(
        "llm-gateway-platform-fallback-needs-an-account",
        "LLM platform-key fallback does nothing unless an operator account is configured.",
        "allow_platform_fallback only has an effect when the platform owner variable names "
        "a real provisioned account; otherwise the caller's own BYO error surfaces unchanged.",
        "llm", "crates/builtins/services/llm-gateway/src/config.rs", "test:fallback_requires_configured_account",
    ),
    claim(
        "grpc-web-status-reads-zero-on-failure",
        "A failed gRPC-Web call is metered status zero, not its real error.",
        "The status extractor cannot see gRPC-Web's trailer frame, so a failing gRPC-Web "
        "call is currently counted as status 0 / Success in usage events.",
        "grpc", "crates/boogy-host/src/grpc_edge.rs", "test:grpc_web_failure_status_gap",
    ),
    claim(
        "frontend-assets-are-content-addressed",
        "A deployed frontend's assets are stored content-addressed, keyed by hash.",
        "Every asset the bundler produces is written to the blob store under a hash of "
        "its own bytes, and the asset map that resolves routes to assets is itself keyed by that hash.",
        "frontend", "crates/boogy-frontend/src/assets.rs", "test:frontend_assets_content_addressed",
    ),
    claim(
        "request-stream-reconnect-reruns-the-handler",
        "A reconnecting SSE client re-runs the handler from scratch.",
        "Nothing reads Last-Event-ID on a request-scoped stream, so a standards-compliant "
        "client's automatic reconnect re-issues the whole request and mints a fresh stream id.",
        "streaming", "crates/boogy-host/src/stream_response.rs", "test:stream_reconnect_reruns_handler",
    ),
    claim(
        "audit-log-never-stores-secret-values",
        "An audit event for a bound secret never records the secret's value.",
        "secret.bound and secret.removed audit rows carry the secret's name and owner, "
        "never the bytes; a leaked audit table cannot leak a credential.",
        "audit", "crates/boogy-audit/src/actions.rs", "test:audit_never_carries_secret_value",
    ),
]

# (query, expected top hit). Wording is natural-language, the way an agent
# actually asks — none of these are the claim's own id. Marked with the
# falsifier set below where a literal substring search finds nothing.
POSITIVES = [
    ("how are background jobs priced", "background-job-calls-are-priced"),
    ("what happens on a ledger commit conflict", "ledger-conflict-returns-409"),
    ("does a ledger reservation ever expire", "ledger-reservations-expire-after-lease"),
    ("can I publish to a websocket channel that is not declared", "ws-publish-needs-a-declared-channel"),
    ("what happens when a deploy's schema conflicts with the stored one",
     "schema-conflict-blocks-provision"),
    ("when does a dropped column's storage actually get freed",
     "dropped-column-bytes-stay-until-reclaimed"),
    ("does boogy use jwt for its tokens", "auth-tokens-are-paseto-not-jwt"),
    ("is my api key stored in plaintext", "api-keys-are-hashed-not-stored"),
    ("is the rate limit shared across callers of a service",
     "rate-limit-buckets-by-caller-not-service"),
    ("does a transaction conflict retry automatically", "tx-commit-conflict-is-409-no-auto-retry"),
    ("can a callee see my auth cookie on a peer call", "peer-fetch-strips-identity-headers"),
    ("is there a limit on how many rows an unindexed scan can read",
     "store-scan-caps-at-five-million-rows"),
    ("what stops a guest from spinning forever", "cpu-deadline-traps-a-spinning-guest"),
    ("can two hosts sweep the same service's dropped columns at once",
     "reclamation-sweeper-locks-one-service-at-a-time"),
    ("does a websocket connection close when a subscription grant expires",
     "websocket-grant-expiry-releases-membership-not-connection"),
    ("will the llm gateway fall back to a platform key automatically",
     "llm-gateway-platform-fallback-needs-an-account"),
    ("is a failed grpc-web call counted correctly in metrics",
     "grpc-web-status-reads-zero-on-failure"),
    ("how are frontend assets addressed in storage", "frontend-assets-are-content-addressed"),
]

# A subset of POSITIVES that a literal substring search — the search that
# exists today — scores zero on: multi-word questions that never appear as
# an exact phrase in any claim's haystack. Asserted below against a baseline
# that mirrors today's `cmd_search` matching, so this benchmark cannot pass
# on pre-feature behaviour (spec §4).
FALSIFIER_QUERIES = [
    "how are background jobs priced",
    "what happens when a deploy's schema conflicts with the stored one",
    "does a transaction conflict retry automatically",
]

# Queries whose answer is genuinely absent from the fixture store: none of
# these facts are claimed anywhere above, so each must report no hits at
# all — the negative half spec §4 requires, without which a system that
# always answers something would pass.
NEGATIVES = [
    "is quantum resistant encryption on boogy",
    "customers receive a refund when overcharged",
    "boogy migrates a mysql instance directly",
    "boogy sends mobile push notifications",
    "boogy is a graphql server",
]


def _substring_baseline(claims, query):
    """Today's `cmd_search`: a case-insensitive literal substring against
    the joined id/area/body/sources/evidence haystack. Reimplemented here
    (not imported from cli.py) because Task 1 is pure and must not depend
    on the CLI; this mirrors `cmd_search`'s haystack construction exactly
    enough to prove the falsifiers are genuinely new capability."""
    q = query.lower()
    hits = []
    for cid, fields in claims:
        hay = "\n".join([fields["id"], fields["area"], fields["body"], fields["src"], fields["ev"]]).lower()
        if q in hay:
            hits.append(cid)
    return hits


class Benchmark(unittest.TestCase):
    def setUp(self):
        self.index = Index(CLAIMS)

    def test_falsifiers_score_zero_on_literal_substring_search(self):
        """Proves the benchmark cannot pass on pre-feature behaviour: these
        exact queries find nothing under today's substring search."""
        for query in FALSIFIER_QUERIES:
            with self.subTest(query=query):
                self.assertEqual(_substring_baseline(CLAIMS, query), [],
                                  f"expected the substring baseline to find nothing for {query!r}, "
                                  f"so it is not a valid falsifier")

    def test_positives_meet_the_rank1_and_top3_floor(self):
        rank1_hits = 0
        top3_hits = 0
        misses = []
        for query, expected in POSITIVES:
            results = search(self.index, query, limit=3)
            ids = [cid for _, cid in results]
            if ids[:1] == [expected]:
                rank1_hits += 1
            if expected in ids:
                top3_hits += 1
            else:
                misses.append((query, expected, ids))
        n = len(POSITIVES)
        rank1_rate = rank1_hits / n
        top3_rate = top3_hits / n
        self.assertGreaterEqual(
            rank1_rate, RANK1_FLOOR,
            f"rank-1 rate {rank1_rate:.0%} ({rank1_hits}/{n}) is below the {RANK1_FLOOR:.0%} floor; "
            f"misses: {misses}",
        )
        self.assertGreaterEqual(
            top3_rate, TOP3_FLOOR,
            f"top-3 rate {top3_rate:.0%} ({top3_hits}/{n}) is below the {TOP3_FLOOR:.0%} floor; "
            f"misses: {misses}",
        )

    def test_negatives_return_no_hits(self):
        for query in NEGATIVES:
            with self.subTest(query=query):
                self.assertEqual(search(self.index, query), [],
                                  f"expected no hits for absent-answer query {query!r}")

    def test_falsifiers_rank_correctly_under_the_real_index(self):
        """The queries proven zero under substring search (above) must
        actually work under ranking — the point of the feature."""
        by_query = dict(POSITIVES)
        for query in FALSIFIER_QUERIES:
            expected = by_query[query]
            with self.subTest(query=query):
                results = search(self.index, query, limit=3)
                ids = [cid for _, cid in results]
                self.assertIn(expected, ids, f"{query!r} should surface {expected!r} in the top 3, got {ids}")


def _synthetic_claims(n, seed=1234):
    """`n` generated claims with phrase-like ids and varied natural-ish
    text, for the cost benchmark (spec §5) — not meant to be individually
    meaningful, just realistically shaped and varied enough that postings
    aren't trivially degenerate."""
    rng = random.Random(seed)
    nouns = ["job", "ledger", "channel", "schema", "token", "scan", "guest", "sweeper",
             "gateway", "asset", "stream", "secret", "deploy", "retry", "grant", "lease",
             "conflict", "column", "cache", "budget", "handler", "socket", "queue", "index"]
    verbs = ["expires", "reconciles", "blocks", "retries", "reclaims", "publishes",
             "validates", "rejects", "records", "bounds", "resolves", "drops"]
    adjs = ["declared", "unindexed", "background", "private", "bounded", "shared",
            "reserved", "stale", "fresh", "signed", "content-addressed", "per-service"]
    claims = []
    for i in range(n):
        words = [rng.choice(adjs), rng.choice(nouns), rng.choice(verbs), rng.choice(nouns)]
        cid = f"{'-'.join(words)}-{i}"
        body_words = [rng.choice(nouns) for _ in range(6)] + [rng.choice(verbs) for _ in range(3)]
        body = " ".join(body_words) + f" claim number {i}"
        claims.append(claim(cid, " ".join(words), body, rng.choice(nouns),
                             f"crates/example/src/{rng.choice(nouns)}.rs", f"test:case_{i}"))
    return claims


class Cost(unittest.TestCase):
    def test_build_and_query_cost_at_500_claims(self):
        """spec §5: under 50 ms to build a fresh Index and run a query over
        500 claims — the whole per-invocation cost, since rebuilding from
        scratch on every call is the design (spec §2), not a shortcut we
        happen to take. If this exceeds the budget, the answer is to report
        the number, not to add a cache (R3)."""
        claims = _synthetic_claims(500)
        started = time.perf_counter()
        index = Index(claims)
        results = search(index, "what happens when a background job retries", limit=10)
        elapsed = time.perf_counter() - started
        print(f"\n[test_search_ranking] build+query at 500 claims: {elapsed * 1000:.2f} ms "
              f"({len(results)} hits)")
        self.assertLess(
            elapsed, COST_BUDGET_SECONDS,
            f"build+query at 500 claims took {elapsed * 1000:.2f} ms, over the "
            f"{COST_BUDGET_SECONDS * 1000:.0f} ms budget (spec §5) — report this, do not add a cache",
        )


if __name__ == "__main__":
    unittest.main()
