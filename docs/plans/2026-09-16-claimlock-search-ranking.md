# claimlock ranked search — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `claimlock search` answers the questions an agent actually asks — ranked by relevance, and honestly silent when no claim covers the topic.

**Architecture:** A new pure module `lib/claimlock/rank.py` (tokenisation, corpus-aware folding, per-field BM25, an absence-based relevance floor) with no I/O, built fresh on every invocation — no persisted index, therefore no new staleness surface. `cmd_search` consumes it and gains `--top` and `--literal`. Everything else in claimlock is untouched.

**Tech Stack:** Python ≥ 3.11 standard library, `unittest`.

**Spec:** `docs/specs/2026-09-16-claimlock-search-ranking-design.md` — read all of it before either task, especially §3.3 (the fixed weights and why they are fixed) and §3.4 (the relevance floor).

## Global Constraints

- Python ≥ 3.11 standard library only. **No new dependency, no network call, no persisted index, no cache file.**
- Tests: `python3 -m unittest discover -s tests` from the repo root — **410** at the start of this plan; `python3 bin/claimlock self-test` stays at 19/19.
- Ranking never affects a verdict: `check`, the hooks, exit codes, counts and summary lines are untouched. `search` keeps exit 0 on a hit and 1 on no match.
- Nothing that is findable today may become unfindable: the indexed fields stay id, area, headline, body, source paths and evidence refs.
- Field weights are **fixed by spec §3.3** (id 3.0, headline 2.0, area 1.5, sources 1.0, body 1.0, evidence 0.5). Do not tune them against the benchmark — that is overfitting to twenty sentences. Changing one requires a stated principle plus before/after benchmark numbers, reported.
- Determinism: equal scores break by claim id, so two clones print the same order.
- The README must list every command in `claimlock --help` (`tests/test_plugin_manifest.py`); skills obey `tests/test_skills_format.py`.
- Revert temporary mutations by re-applying the inverse edit, never `git checkout --`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv
  ```

## File map

| File | Change |
|---|---|
| `lib/claimlock/rank.py` | NEW: `tokens`, `fold`, `Index`, `search` |
| `tests/test_rank.py` | NEW (Task 1): unit tests for the pure logic |
| `tests/test_search_ranking.py` | NEW (Task 1): the benchmark, positives + negatives + falsifier |
| `lib/claimlock/cli.py` | Task 2: `cmd_search` ranks; `--top`, `--literal`; the no-match message |
| `README.md`, `docs/format.md`, `skills/using-claimlock/SKILL.md` | Task 2 |

## Shared interfaces (exact names)

```python
# rank.py
def tokens(text: str) -> list[str]
def fold(terms: list[str], vocab: set[str]) -> list[str]
FIELDS = ("id", "head", "area", "src", "body", "ev")
WEIGHTS = {"id": 3.0, "head": 2.0, "area": 1.5, "src": 1.0, "body": 1.0, "ev": 0.5}
K1, B, ABSENCE_FLOOR = 1.2, 0.75, 0.5
STOP_WORDS: frozenset[str]              # ~60 English function words (spec §3.4)

class Index:
    def __init__(self, docs: list[tuple[str, dict[str, str]]]): ...   # [(claim id, {field: text})]
    vocab: set[str]
    def unknown(self, terms) -> list[str]     # query terms absent from the corpus, in order
def search(index: Index, query: str, limit: int | None = None) -> list[tuple[float, str]]
```

---

### Task 1: `rank.py` and the benchmark

**Files:**
- Create: `lib/claimlock/rank.py`, `tests/test_rank.py`, `tests/test_search_ranking.py`

**Interfaces:** Produces everything in Shared interfaces. Consumes nothing (pure module — it must not import `claims`, `gitio`, or touch the filesystem).

Behaviour: spec §3.1–§3.4 and §4.

- [ ] **Step 1: Write the failing unit tests** in `tests/test_rank.py`:
  - `tokens`: `"background-job-calls-are-priced"` → five terms; punctuation, mixed case and `src/limit.py` all split on non-alphanumerics; an empty string → `[]`.
  - `fold`: `"jobs"` → `"job"` when `job` is in the vocabulary, and stays `"jobs"` when it is not; `-es`, `-ed`, `-ing`→`e`, `-ing` each fold only when the candidate stem is in the vocabulary; a term already in the vocabulary is never folded.
  - `Index`/`search`: a term appearing in every document contributes ~nothing (IDF), so a query of only ubiquitous terms yields no hit; a rarer term outranks a common one; a match in `id` outranks the same match in `body` (weights); equal scores order by claim id (build two claims scoring identically and assert the order twice).
  - The floor (§3.4): a query at least half of whose content terms are absent from the corpus returns **no** hits — e.g. with `payment` absent, `"what happens when a payment fails"` is silent even though `fails` is present; `index.unknown(...)` returns `["payment"]`.
  - `limit` bounds the result; `None` means everything.
- [ ] **Step 2: Run** `cd tests && python3 -m unittest test_rank -v` — expect failures naming the missing module.
- [ ] **Step 3: Implement** `rank.py` per spec §3.1–§3.4. Keep it pure and free of I/O so it is testable without a store. BM25 exactly as §3.3 writes it.
- [ ] **Step 4: Write the benchmark** in `tests/test_search_ranking.py` per spec §4: a fixture store of realistic claims (write them in the test; model them on a real store — phrase-like ids, one fact each, a few sharing a topic), then
  - **≥15 positives** `(query, expected id)`: assert ≥80% at rank 1 and 100% within the top 3, with the measured rates in the failure message;
  - **≥5 negatives**: a query whose answer is genuinely absent must return no hits;
  - **≥3 falsifiers**: multi-word questions that a literal substring search scores zero on — assert in the same test that the substring baseline finds nothing for them, so the benchmark cannot pass on pre-feature behaviour.
- [ ] **Step 5: Measure cost** (spec §5): build and query an index over 500 generated claims; assert under 50 ms and print the measured value. If it exceeds the budget, report it — do not add a cache.
- [ ] **Step 6: Run** the focused tests, then the full suite; commit `feat(rank): BM25 search ranking with a relevance floor`.

### Task 2: Wire it into `search`, and the docs

**Files:**
- Modify: `lib/claimlock/cli.py`, `README.md`, `docs/format.md`, `skills/using-claimlock/SKILL.md`
- Test: `tests/test_output_budget.py` and `tests/test_cli_read.py` (existing `search` assertions), plus new cases

**Interfaces:** Consumes Task 1's `rank`. Produces the `--top` and `--literal` flags.

Behaviour: spec §3.5.

- [ ] **Step 1: Write the failing tests**:
  - Ranked order: in a store where one claim's id matches the query and another only mentions it deep in its body, the id match prints first.
  - `--top`: default 10; a store with 15 matches prints 10 then exactly `… and 5 more — claimlock search <query> --top 15`; `--top 0` or a negative value is refused with exit 2 rather than printing nothing silently.
  - `--literal`: restores substring behaviour — `claimlock search --literal 'src/limit.py'` finds the claim citing that path, and a multi-word query that ranks fine returns the old zero hits under `--literal`.
  - No match: exit 1, and the message names absent query terms (`(no claim mentions: payment)`); with every term present but none discriminating, the message omits that clause.
  - `--body` still prints matching body lines, now under ranked hits.
  - Exit codes unchanged (0 on hit, 1 on no match); the output-budget ceiling for `search` still holds.
- [ ] **Step 2: Run** them — expect failures.
- [ ] **Step 3: Implement** per spec §3.5. Build the `Index` from the same claims `cmd_search` already loads; do not add a second load or any caching.
- [ ] **Step 4: Run** focused then full suite plus `python3 bin/claimlock self-test`; update the existing `search` assertions the new ordering breaks, and only those.
- [ ] **Step 5: Docs.** README's `claimlock search` row and the Output size table's `search` line; `docs/format.md`'s search section (ranking, the floor, `--top`, `--literal`, the no-match message); one line in `skills/using-claimlock/SKILL.md` telling the agent it can ask a question in words and that an empty result now means *no claim covers this*, which is itself a finding worth reporting rather than working around.
- [ ] **Step 6: Commit** `feat(search): ranked results, --top and --literal`.
