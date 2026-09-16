# claimlock — output budgets and the cheap path

Status: approved 2026-09-15 (design and target set approved by the user after
measurement). Base specs: `2026-09-14-claimlock-design.md`,
`2026-09-14-claimlock-teams-design.md` (T1–T15),
`2026-09-15-claimlock-regions-renames-design.md` (T13–T15 there).

## 1. Problem

Every claimlock command prints in full. An agent reading that output pays for
it in context on every invocation, and nothing in the skills points at the
cheap command. Measured 2026-09-15 against a real 41-claim store (Boogy's
`docs/truth`, imported) and a synthetic 201-claim store:

| Command | Output | Where it goes |
|---|---|---|
| `check` (41 claims, all failing) | 16,739 B ≈ 4,200 tok | 6,929 B of it (41%) is the same hint text, once per claim; 1,927 B verdict lines; 7,774 B per-source lines (137 lines) |
| `search test` (38 hits) | 11,268 B ≈ 2,800 tok | 8,518 B (76%) matching body lines; 2,712 B headers |
| `show ledger-conserves-money` | 16,188 B ≈ 4,000 tok | whole body (10,067 B) + evidence (5,693 B) |
| `check --json` (41 claims) | 26,193 B ≈ 6,500 tok | every claim, passing ones included (63,401 B at 201 claims) |
| `diff` (large file rewritten) | 44,465 B ≈ 11,100 tok | unbounded; a one-line change is 181 B |
| `docs/format.md` read | 52,821 B ≈ 13,200 tok | nothing tells an agent not to read it routinely |
| `README.md` read | 35,248 B ≈ 8,800 tok | same |
| `using-claimlock` + `operating-claimlock` | 21,199 B ≈ 5,300 tok | loaded in full when invoked |

Two structural findings from the same measurements:

- **Churn:** one edit to a shared 2,400-line file made **50** whole-file claims
  stale in the synthetic store, while the region-pinned claim on that same file
  stayed `fresh`.
- **Speed is not the problem:** every command completes in ~0.2 s at 201 claims,
  cold or warm cache. No git or hashing work is in scope here.

## 2. Principle

Every command prints a **bounded** report by default and keeps an explicit
escape hatch (`--full`, or `--body` for `search`) that restores today's
complete output. Exit codes, verdicts and counts never change: only how much
detail is printed. A bounded report always says what it withheld and how to
see it, so nothing becomes unreachable and no reader is misled into thinking
they saw everything.

## 3. Budgets

Constants live in `lib/claimlock/cli.py` beside the formatting that uses them.

| Constant | Default | Applies to |
|---|---|---|
| `LISTED_CLAIMS` | 20 | failing claims listed by `check` |
| `SOURCE_LINES` | 3 | per-source detail lines per claim in `check` |
| `BODY_LINES` | 40 | body lines in `show` |
| `EVIDENCE_CHARS` | 200 | each evidence `ref` in `show` |
| `DIFF_LINES` | 200 | diff lines per source in `diff` |
| `HEADLINE_CHARS` | 120 | headline in `search` and `list` |

### 3.1 `check`

- Each state's hint prints **once**, in a `hints:` block after the claim list,
  as `  <state>: <hint text>` with `<id>` replaced by the first claim in that
  state. The per-claim hint line is gone. This applies with and without
  `--full`: it is pure duplication.
- At most `SOURCE_LINES` per-source lines per claim, then
  `         … and <n> more sources — claimlock check --full`.
- At most `LISTED_CLAIMS` failing claims, then
  `… and <n> more failing claims — claimlock check --full`.
- The `pre-existing (not changed here):` list and the `OWED` lines keep their
  shape but obey `LISTED_CLAIMS` the same way.
- The summary line is unchanged.
- `--full`: every claim, every source line; hints still once.

### 3.2 `check --json`

- `results` holds **blocking** claims (those with problems, or a non-fresh
  state, in scope), plus **failing claims outside the `--changed` scope**
  (pre-existing drift a `--changed` reader still needs to see), plus `owed`
  claims — a result's `blocking` field is therefore `false` for the last two
  groups. A new top-level `"omitted"` gives the number of claims left out.
- `--full` restores every claim, and sets `"omitted": 0`.
- `claims`, `sources_hashed`, `counts` and `scope` are unchanged, so a reader
  that only wants totals sees the same numbers.
- This is a breaking change for a consumer that parsed every claim from
  `--json`; the README's migration section says so and names `--full`.

### 3.3 `search`

- Default: one line per hit —
  `<id> (<area>, <status>)[ [<state>]]  <headline truncated to HEADLINE_CHARS>`.
  No body lines, no blank separator line.
- `--body`: today's output (matching body lines indented under each hit).
- The no-match message and exit 1 are unchanged.

### 3.4 `show`

- The body is capped at `BODY_LINES` lines; the cut prints
  `… <n> more lines — read <claim path>` (the same relative path the trailing
  `file:` line names). Deliberate exception to the "and" form below: this cut
  names a path rather than a flag and reads as a sentence, so it keeps its
  own wording rather than matching `check`'s and `diff`'s.
- Each evidence `ref` is truncated to `EVIDENCE_CHARS` with a trailing `…`.
- Status, state, problems, the source list and the `file:` line are unchanged —
  the parts a reader acts on are never truncated.
- `--full`: whole body, whole refs.

### 3.5 `diff`

- Per source, at most `DIFF_LINES` lines of unified diff, then
  `… and <n> more diff lines — claimlock diff <id> --full` — the same "and"
  form `check` uses for its own cap notes (§3.1).
- The cap counts the lines printed for that source, headers included, and a
  source that fits prints exactly as today.
- `--full`: no cap.

### 3.6 `list`

Headlines are truncated to `HEADLINE_CHARS`; `--full` prints them whole.
Everything else is unchanged.

## 4. The cheap path (skills)

Both claimlock skills gain the same short sequence, stated as the default way
to work:

1. After editing files: `claimlock affected <paths you touched>` — it lists
   only the claims citing those paths.
2. Before committing, once: `claimlock check --changed <base>`.
3. `claimlock diff <id>` only for the claim you are about to verify.
4. `claimlock search <topic>` reads one line per hit; add `--body` only when
   the headline is not enough.

And two prohibitions:

- **Do not read `docs/format.md` or `README.md` for routine claim work.** They
  are reference for changing claimlock itself and cost ~13,200 and ~8,800
  tokens. Everything the routine flows need is in the skills.
- **Do not run `check --json`** unless a machine is parsing it; the text form
  is smaller.

`using-claimlock` also strengthens the region-pin guidance with the
measurement: on the synthetic store, one edit to a shared file staled 50
whole-file claims and left the region-pinned claim fresh.

## 5. Skill trimming

`using-claimlock` (11,087 B) and `operating-claimlock` (10,112 B) come down
toward a combined ~14,000 B by moving worked examples and format detail into
`docs/format.md`, keeping **every rule and every red-flag row**. This lands as
its own task and its own reviewed change, because the risk is cutting a rule
that was doing work. A rule that is only in the skills stays in the skills.

**Outcome** (measured 2026-09-15, `wc -c` on both `SKILL.md` files): the trim
landed at 20,545 B combined (11,034 + 9,511), down from 21,199 B — short of
the ~14,000 B target because keeping every rule and every red-flag row
outranked the byte target.

## 6. Testing

Per command: a bounded-default test asserting the exact new shape, a `--full`
test asserting the complete output is still reachable, and an **output budget**
test over a generated 30-claim fixture asserting the byte size of the default
report stays under a stated ceiling (a regression guard, since the whole point
is size). Budget ceilings are asserted as `<=`, with the measured value in the
failure message.

## 7. Non-goals

Git or hashing optimization (0.2 s at 201 claims); the `refs` scan's I/O (804
tracked files, 15 MB per run, 52 B of output); a new composite command (terse
`search` is that); paging or interactive output; changing hook output (already
capped at 2,000 characters).

## 8. Risks

- **A bounded default hides a finding.** Mitigated by: exit codes and counts
  never change, every cut names what it withheld and the flag that shows it,
  and the summary line always reports the full totals.
- **`--json` shape change** breaks a consumer that read every claim. Named in
  the README migration section; `--full` restores it.
- **Skill trimming drops a load-bearing rule.** Mitigated by keeping every rule
  and red-flag row, doing it as a separate reviewed task, and diffing rule
  counts before and after.
