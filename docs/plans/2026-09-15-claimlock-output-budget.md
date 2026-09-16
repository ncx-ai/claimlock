# claimlock output budgets and the cheap path — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every claimlock command prints a bounded report by default, with `--full` (or `--body`) restoring today's complete output, and both claimlock skills prescribe the cheap command sequence instead of the expensive one.

**Architecture:** Formatting-only changes in `lib/claimlock/cli.py` (budget constants plus small truncation helpers) — no change to `claims.py` evaluation, exit codes, counts or hook output. Skills change in their own task. A new `tests/test_output_budget.py` asserts both the exact bounded shapes and byte ceilings over a generated 30-claim fixture.

**Tech Stack:** Python ≥ 3.11 standard library, `unittest`.

**Spec:** `docs/specs/2026-09-15-claimlock-output-budget-design.md` — read all of it before any task. Base specs: `docs/specs/2026-09-14-claimlock-design.md`, `docs/specs/2026-09-14-claimlock-teams-design.md`, `docs/specs/2026-09-15-claimlock-regions-renames-design.md`.

## Global Constraints

- Python ≥ 3.11 standard library only in `lib/`; do not touch `bin/claimlock`.
- Tests: `python3 -m unittest discover -s tests` from the repo root must pass; the suite is **336** at the start of this plan. `python3 bin/claimlock self-test` must stay at 19/19. A git-clone test has flaked once with exit 128; if a failure is exactly that, rerun once and note it.
- **Exit codes, verdicts, counts and the summary line never change.** Only printed detail is bounded. `check` still exits 1 on any blocking claim even when the claim is not among those listed.
- **No information may become unreachable:** every truncation names what it withheld and the flag that shows it (`--full`, or `--body` for `search`).
- Hook output is out of scope (already capped at 2,000 characters) — do not change `hooks.py`.
- No git or hashing changes; no new git subprocess.
- Budget constants live in `lib/claimlock/cli.py`: `LISTED_CLAIMS = 20`, `SOURCE_LINES = 3`, `BODY_LINES = 40`, `EVIDENCE_CHARS = 200`, `DIFF_LINES = 200`, `HEADLINE_CHARS = 120`.
- The README must list every command in `claimlock --help` as `claimlock <cmd>` (`tests/test_plugin_manifest.py`); skills obey `tests/test_skills_format.py`.
- Revert temporary mutations by re-applying the inverse edit, never `git checkout --`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv
  ```

## File map

| File | Change |
|---|---|
| `lib/claimlock/cli.py` | budget constants; `_hints_block`, `_capped`; `cmd_check` (text + `--json`), `cmd_search`, `cmd_show`, `cmd_list`, `cmd_diff`; `--full` / `--body` flags |
| `tests/test_output_budget.py` | NEW: fixture generator, exact-shape tests, `--full` parity, byte ceilings |
| `tests/test_cli_read.py`, `tests/test_team_gate.py`, `tests/test_renames.py`, `tests/test_regions.py`, `tests/test_verify_diff.py` | update assertions that depend on the old shapes (per task) |
| `README.md`, `docs/format.md` | per task: the commands that task bounded, plus the `--json` migration note |
| `skills/using-claimlock/SKILL.md`, `skills/operating-claimlock/SKILL.md` | Task 4 |

## Shared interfaces (exact names)

```python
# cli.py
LISTED_CLAIMS, SOURCE_LINES, BODY_LINES, EVIDENCE_CHARS, DIFF_LINES, HEADLINE_CHARS

def _capped(items, cap, more):
    """(kept, note | None): items[:cap], and `more.format(n=<left over>)` when cut."""

def _hints_block(states_in_order, first_id):
    """The `hints:` lines: one per state present, `  <state>: <HINT[state] with {id}
    replaced by first_id[state]>`. [] when no state is present."""

def _clip(text, chars):
    """text unchanged, or text[:chars-1] + "…"."""
```

---

### Task 1: `check` — hints once, capped claims and sources; bounded `--json`

**Files:**
- Modify: `lib/claimlock/cli.py` (`cmd_check`, `_print_failing`, `_owed_line` call sites, `build_parser`), `README.md` (the `claimlock check` row and the CI section if it shows output; the migration note for `--json`), `docs/format.md` (the `check` and `check --json` sections)
- Create: `tests/test_output_budget.py`
- Update: any existing test asserting a per-claim hint line or full `--json` results (grep `tests/` for `re-check it`, `never pinned`, `"results"`)

**Interfaces:** Produces `LISTED_CLAIMS`, `SOURCE_LINES`, `_capped`, `_hints_block`, and the `--full` flag on `check`. Later tasks add `BODY_LINES`, `EVIDENCE_CHARS`, `DIFF_LINES`, `HEADLINE_CHARS`.

Behaviour: spec §3.1 and §3.2.

- [ ] **Step 1: Write the failing tests** in `tests/test_output_budget.py`:
  - A fixture helper `store(n_claims, sources_per_claim=3, body_lines=4)` building a plain-directory store (`make_repo(use_git=False)`), verifying every claim, then making every source stale (rewrite each source), so every claim is blocking.
  - `check` on a 30-claim store:
    - prints at most `LISTED_CLAIMS` verdict blocks, then exactly
      `… and 10 more failing claims — claimlock check --full`
    - prints a `hints:` block containing exactly one `  stale: ` line, whose `<id>` placeholders are the first stale claim's id
    - contains no hint text inside a verdict block (assert the hint substring appears exactly once in the whole output)
    - a claim with 5 stale sources prints 3 source lines then `         … and 2 more sources`
    - an INVALID claim with 5 problems prints 3 then `         … and 2 more problems`
    - exit code is 1, and the summary line still reports all 30 (`30 claims`, `30 stale`)
  - `check --full` on the same store: every claim listed, every source line, still exactly one `stale: ` hint line, exit 1.
  - `check --json` on the same store: `results` length equals the blocking count, `omitted` equals `claims - len(results)`, `counts`/`claims`/`sources_hashed` unchanged; with one fresh claim added, that claim is absent by default and present under `--full` with `"omitted": 0`.
  - `check --json` on a store with an `owed` claim: the owed claim is in `results` by default.
  - **Budget ceilings** (regression guards; assert `<=` with the measured value in the message): default `check` on the 30-claim store ≤ 4,000 B; `check --json` ≤ 3,000 B. Record the actual measured sizes in the test's failure message.
- [ ] **Step 2: Run** `cd tests && python3 -m unittest test_output_budget -v` — expect failures naming the missing flag and the old shapes.
- [ ] **Step 3: Implement** per spec §3.1–§3.2 in `cli.py`: constants, `_capped`, `_hints_block`, `_clip`; `_print_failing` takes the caps and stops emitting hints; `cmd_check` collects the states it printed (in `("invalid", *C.NON_FRESH)` order), prints the claim list, the pre-existing list, the OWED lines (each capped by `LISTED_CLAIMS`), then the hints block, then the summary; `--json` filters `results` and adds `omitted`; `p.add_argument("--full", action="store_true")` on `check`.
- [ ] **Step 4: Run** the focused tests, then the full suite; update the existing assertions the new shape breaks.
- [ ] **Step 5: Docs.** README's `claimlock check` row gains "prints a bounded report — `--full` for every claim and source"; the Migrating section gains a `--json` bullet naming the `results`/`omitted` change and `--full`. `docs/format.md`'s `check` section documents the caps, the `hints:` block, the `--json` filter and `omitted`, with the exact strings.
- [ ] **Step 6: Commit** `feat(cli): bounded check report — hints once, capped claims/sources, blocking-only --json`.

### Task 2: `search`, `show` and `list` — one line per hit, capped body and refs

**Files:**
- Modify: `lib/claimlock/cli.py` (`cmd_search`, `cmd_show`, `cmd_list`, `build_parser`), `README.md`, `docs/format.md`
- Test: `tests/test_output_budget.py` (add classes), and update existing assertions (grep `tests/` for `search`, `show`)

**Interfaces:** Consumes Task 1's `_clip`, `_capped`. Produces `BODY_LINES`, `EVIDENCE_CHARS`, `HEADLINE_CHARS`, `search --body`, `show --full`, `list --full`.

Behaviour: spec §3.3, §3.4, §3.6.

- [ ] **Step 1: Write the failing tests:**
  - `search`: on a store whose claims have a 6-line body each matching the query, the output has exactly one line per hit, of the form `<id> (<area>, <status>)  <headline>`, with no indented body lines; a claim in a non-fresh state shows ` [stale]` before the two spaces; a 200-character headline is cut to 120 with a trailing `…`; `--body` restores the indented matching lines; no-match still prints `claimlock: nothing matches 'x'` and exits 1; a hit's exit code is 0.
  - `show`: a claim with a 60-line body prints 40 lines then `… 20 more lines — read claims/<id>.md`; an evidence `ref` of 500 characters prints 199 characters plus `…`; the status line, any `INVALID`/state line, the source list and the trailing `file:` line are byte-identical to today; `--full` prints the whole body and whole refs.
  - `list`: a 200-character headline is cut to 120 with `…`; `--full` prints it whole; the status/flag line is unchanged.
  - Budget ceilings: `search` matching 30 claims ≤ 3,000 B; `show` on a claim with a 60-line body and three 500-character refs ≤ 3,000 B.
- [ ] **Step 2: Run** them — expect failures.
- [ ] **Step 3: Implement** per spec §3.3/§3.4/§3.6.
- [ ] **Step 4: Run** focused then full suite; update broken assertions.
- [ ] **Step 5: Docs.** README rows for `search`, `show`, `list` name the bounded default and the flag; `docs/format.md` documents the shapes and exact truncation strings.
- [ ] **Step 6: Commit** `feat(cli): bounded search/show/list output`.

### Task 3: `diff` — cap lines per source

**Files:**
- Modify: `lib/claimlock/cli.py` (`cmd_diff`, `build_parser`), `README.md`, `docs/format.md`
- Test: `tests/test_output_budget.py` (add a class), update `tests/test_verify_diff.py` / `tests/test_regions.py` assertions if the cap changes their output (it should not — their diffs are small)

**Interfaces:** Consumes Task 1's `_capped`. Produces `DIFF_LINES`, `diff --full`.

Behaviour: spec §3.5.

- [ ] **Step 1: Write the failing tests** (git repo):
  - A claim pinning a 900-line file, then the whole file rewritten: `diff <id>` prints at most `DIFF_LINES` lines for that source, and the last line for it is `… <n> more diff lines — claimlock diff <id> --full`; `n` equals the lines withheld.
  - `diff <id> --full` prints the complete unified diff (byte-identical to today's output).
  - A small change (one line) prints exactly as today, with no cap note.
  - A claim with two stale sources caps each source independently.
  - A region source's diff obeys the same cap.
  - Budget ceiling: the rewritten-900-line case ≤ 12,000 B by default (it is ~44,000 B today).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** per spec §3.5: build each source's diff lines, then cap and note.
- [ ] **Step 4: Run** focused then full suite.
- [ ] **Step 5: Docs.** README `claimlock diff` row and `docs/format.md`'s `diff` section name the cap and `--full`.
- [ ] **Step 6: Commit** `feat(cli): cap diff output per source`.

### Task 4: The cheap path in both skills, and trimming

**Files:**
- Modify: `skills/using-claimlock/SKILL.md`, `skills/operating-claimlock/SKILL.md`, `docs/format.md` (receives any detail moved out of the skills), `README.md` (only if a statement there contradicts the new guidance)

**Interfaces:** Consumes Tasks 1–3 (the flags and bounded shapes the guidance names must already exist).

Behaviour: spec §4 and §5.

- [ ] **Step 1: Add the cheap path** to both skills, as the default way to work (spec §4): `affected <paths>` after editing; one `check --changed <base>` before committing; `diff <id>` only for the claim about to be verified; `search` reads one line per hit, `--body` only when needed. Include both prohibitions: do not read `docs/format.md` or `README.md` for routine claim work (~11,200 and ~8,500 tokens; the skills carry what the flows need), and do not run `check --json` unless a machine parses it.
- [ ] **Step 2: Strengthen the region guidance** in `using-claimlock` with the measurement from spec §1: one edit to a shared file staled 50 whole-file claims while the region-pinned claim stayed fresh.
- [ ] **Step 3: Add red-flag rows**: "I'll read format.md to be sure" → "The skills carry every routine rule; format.md is reference for changing claimlock itself, and costs ~11k tokens."; "I'll run check after each edit" → "Run `claimlock affected <paths>` while working and one `check --changed <base>` before committing."
- [ ] **Step 4: Trim** (spec §5). Before editing, count the rules and red-flag rows in each skill and write the counts in your report; after trimming, the counts must be identical. Move worked examples and format detail to `docs/format.md` (adding it there if it is not already covered). Target combined ≈ 14,000 B, from 21,199 B — get as close as the "keep every rule" constraint allows and report the actual sizes.
- [ ] **Step 5: Run** `python3 -m unittest discover -s tests` (skills format tests must pass: frontmatter shape, "Use when" description ≤500 chars, no forbidden terms, every named command exists) and `python3 bin/claimlock self-test`.
- [ ] **Step 6: Commit** `docs(skills): the cheap path, region guidance, and trimming`.
