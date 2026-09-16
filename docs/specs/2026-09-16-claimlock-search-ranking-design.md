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
becomes six terms. A query term absent from the corpus vocabulary is folded by
trying, in order, `-s`, `-es`, `-ed`, `-ing`→`e`, `-ing`, and is rewritten
**only if the candidate stem is itself a term in this corpus**. So "jobs" →
"job" because `job` exists here; nothing is stemmed on linguistic faith, and no
stop-word list is needed — BM25's IDF already drives "how", "are" and "the"
toward zero because they appear nearly everywhere.

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

Ranking always returns *something*. The prototype's one bad result proves the
risk: "what happens when a payment fails" confidently returned
`ws-publish-needs-a-declared-channel`, matching only on "when"/"fails", because
no claim covers payment failure at all. Replacing today's honest zero with a
confident wrong answer would make this feature a net harm.

So a claim qualifies as a hit only if it matches at least one **discriminating**
query term — one whose document frequency is ≤ 25% of the corpus. If no claim
matches any discriminating term, `search` reports no match, exactly as today.

Query terms absent from the corpus entirely are named in the no-match message,
turning a silent miss into a useful one:

```
claimlock: nothing matches 'what happens when a payment fails' (no claim mentions: payment)
```

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
