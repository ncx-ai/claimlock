# claimlock — ranked search

Status: approved 2026-09-16 (design presented with prototype evidence and
approved by the user). Base specs: `2026-09-14-claimlock-design.md`,
`2026-09-14-claimlock-teams-design.md`, `2026-09-15-claimlock-regions-renames-design.md`,
`2026-09-15-claimlock-output-budget-design.md`, `2026-09-16-claimlock-edit-notice-design.md`.

## 1. Problem, measured

`claimlock search` matches a literal substring against a joined haystack of id,
area, body, sources and evidence refs. Measured 2026-09-16 against a real
41-claim store:

| Query | Hits |
|---|---|
| `stream` | 19 of 41 |
| `pricing` | 10 |
| `ledger` | 6 |
| `how are jobs priced` | **0** |
| `what happens when a payment fails` | **0** |
| `is the ledger consistent` | **0** |

Single words filter but do not discriminate; natural-language questions — the
form an agent actually thinks in — return nothing, even when a claim answering
them exists (`background-job-calls-are-priced` for the first). Claim ids are
phrase-like, so guessing the right substring means already knowing the answer.

The failure is **recall on paraphrase**, and it is the expensive kind: a miss is
indistinguishable from "no claim exists", which is exactly when an agent invents
an unverified fact — the failure claimlock exists to prevent. The skills tell it
to `search` before asserting; today that instruction can silently fail.

## 2. Approach, and why not embeddings

Per-field BM25 over the claim store, built fresh on each invocation. A throwaway
prototype (2026-09-16, same 41-claim store) put the right claim at rank 1 for
five of six natural-language queries, at **2.8 ms per query with the index
rebuilt from scratch every call**.

Rebuilding per call is the design, not a limitation: no persisted index means no
cache invalidation, no vectors in git, no per-clone rebuild — and therefore **no
new staleness surface in a tool whose thesis is that stale caches lie**. It also
keeps the stdlib-only, offline, deterministic install intact, which an embedding
model or API would each break.

## 3. Design

### 3.1 `lib/claimlock/rank.py` — pure, no I/O

```python
def tokens(text: str) -> list[str]        # lowercase, split on [^a-z0-9]+, drop empties
def fold(terms, vocab) -> list[str]       # corpus-aware suffix folding (§3.2)
class Index:                              # built from claims; holds per-field postings,
    def __init__(self, docs): ...         # doc frequencies, average field lengths, vocab
def search(index, query, limit) -> list[tuple[float, str]]   # (score, claim id), desc, ties by id
```

`Index` is built from `(id, area, headline, body, source paths, evidence refs)`
per claim — the same fields today's substring search covers, so nothing becomes
unfindable.

### 3.2 Tokenisation and folding — no linguistics

Terms are lowercased and split on runs of non-alphanumerics; `background-job-calls-are-priced`
becomes five terms. A query term absent from the corpus vocabulary is folded by
trying, in order, `-s`, `-es`, `-ed`, `-ing`→`e`, `-ing`, and is rewritten
**only if the candidate stem is itself a term in this corpus**. So "jobs" →
"job" because `job` exists here; nothing is stemmed on linguistic faith.

An earlier draft of this spec argued no stop-word list was needed, because
IDF would drive "how", "are" and "the" toward zero. Measurement refuted that
at this corpus size: `how` appears in 2% of the real store's claims, so IDF
treats it as highly informative. §3.4 therefore uses a small fixed stop list.

### 3.3 Scoring

Per field `f`, with `k1 = 1.2`, `b = 0.75`:

```
idf_f(t) = ln(1 + (N - n_f(t) + 0.5) / (n_f(t) + 0.5))
score(d, q) = Σ_f w_f · Σ_{t∈q} idf_f(t) · tf·(k1+1) / (tf + k1·(1 - b + b·dl_f/avgdl_f))
```

Field weights, fixed by this spec: **id 3.0, headline 2.0, area 1.5, sources
1.0, body 1.0, evidence 0.5**. They come from the prototype that produced §2's
result and are deliberately **not** tuned against the benchmark in §4 — tuning
weights on the same queries you evaluate with is overfitting to twenty
sentences. A future change to them must report before/after benchmark numbers
*and* give the principle behind the change.

Ties break by claim id, so output is deterministic across clones.

### 3.4 The relevance floor — the safety property

Ranking always returns *something*. Replacing today's honest zero with a
confident wrong answer would make this feature a net harm, so a query is
answered only when **the store has vocabulary for it**: drop stop words, and if
at least half of the remaining content terms are absent from the corpus
entirely, report no match. Otherwise rank normally.

Measured on the real 41-claim store (2026-09-16): **7 of 9** natural-language
questions at rank 1, **5 of 5** unanswerable queries silent. It costs no
recall — every answerable query in the set has an absent-fraction of 0.00, so
the floor never fires on one. It is independent of document frequency, so it
also works at n=1 (§3.6).

**Why not document frequency.** Three df-based rules were built and measured
against that store; all three failed, and the reason is the same each time:

| Rule | Result |
|---|---|
| Qualify on a term with df ≤ 25% (this spec's first design) | Deletes real answers — `grpc` (39%) and `error` (46%) are "too common", so `grpc-error-details-are-bounded-never-fatal` was dropped from the results entirely while an unrelated claim qualified on `handling` (1 claim). Admits junk on rare-but-generic terms. Returns **nothing at all** for any store of ≤3 claims, where every term exceeds 25% by construction. |
| IDF-weighted coverage | No threshold separates: at τ ≤ 0.5 it answers 4 of 5 negatives; at τ = 0.6 recall collapses to 5 of 9. |
| Stop-word term coverage | 2 of 5 negatives still answered — one present generic term (`fails`, `event`) gives coverage 1.0, which no threshold can suppress. |

In 41 claims of terse technical prose, function words are *rare* (`how` in 2%)
and topic words are *common* (`grpc` in 39%), so frequency ranks the function
word as the more informative one. **Absence is a better signal than rarity at
this scale**, which is what the shipped rule uses.

**What it does not solve**, stated plainly rather than papered over:

- A query whose content words all exist in the store, but scattered across
  unrelated claims, is still answered. The floor sees vocabulary, not topicality.
- The stop list is a fixed English set of ~60 words; a store in another language
  gets no benefit from it.
- The 0.5 threshold is measured on one store of 41 claims. It should be
  re-measured against a second real store before it is treated as settled.

### 3.5 CLI

- Results are ordered by score, best first, one line per hit (unchanged shape
  from the output-budget work).
- `--top N` (default 10) bounds the list; today every match prints, which is how
  `search stream` costs ~1,400 tokens for 19 undifferentiated hits. When the cut
  applies: `… and <n> more — claimlock search <query> --top <N>`.
- `--literal` restores today's exact-substring matching, for paths and exact
  strings (`claimlock search --literal 'src/limit.py'`).
- `--body` is unchanged.
- Exit codes are unchanged: 0 on a hit, 1 on no match.

### 3.6 Small stores

The floor must not depend on corpus size: a store with a single claim must find
that claim when queried with its own words. The df-based design failed this
(nothing is findable below 4 claims); the shipped rule has no such threshold,
and a test pins n=1.

## 4. The benchmark, and its negative half

`tests/test_search_ranking.py` builds a fixture store of realistic claims and
asserts, as a floor:

- **Positives** — a query set (≥15) with a known right answer: ≥80% at rank 1,
  100% within the top 3.
- **Negatives** — queries whose answer is genuinely absent (≥5): each must report
  no match. Without these the benchmark rewards a system that always answers,
  which is the failure mode §3.4 exists to prevent.
- **A falsifier**: at least three positives are multi-word questions that
  today's substring search scores zero on, so the benchmark cannot pass on
  pre-feature behaviour.

Aggregate rates are asserted, with the measured value in the failure message.

## 5. Cost

Budget: under 50 ms to build and query at 500 claims, measured and reported. If
it exceeds that, the answer is to report it — **not** to add a persisted index,
which would reintroduce the staleness surface §2 rejects.

## 6. Non-goals

Embeddings, a vector store, any network call or new dependency; persisting the
index; cross-claim semantic dedupe (a separate question); changing what `search`
indexes; changing `check`, the hooks, or any gate — ranking never affects a
verdict or an exit code beyond the existing hit/no-hit distinction.

## 7. Risks

- **A confident wrong answer** replacing an honest zero — mitigated by §3.4 and
  pinned by §4's negative cases.
- **Weight drift by ad-hoc tuning** — mitigated by fixing weights in this spec
  and requiring a principle plus before/after numbers to change them.
- **Scale**: rebuilt per call, so cost grows with the store. Bounded by §5 and
  measured rather than assumed.
