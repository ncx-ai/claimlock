# claimlock — when the gate should run

Status: approved 2026-09-17 (direction approved by the user; the measurement
below came first). Base specs: `2026-09-14-claimlock-design.md`,
`2026-09-15-claimlock-output-budget-design.md`,
`2026-09-16-claimlock-orphans-evidence-design.md`.

## 1. Problem, measured

The skills prescribe the gate **before every commit** (`using-claimlock` step 2,
`operating-claimlock` step 2 and its pre-commit-hook section, and the README's
cheap sequence). Measured 2026-09-17 against a real 49-claim store and the last
400 non-merge commits of the repository it documents:

| Gate cadence | Claim surfacings | vs per-commit |
|---|---:|---:|
| per commit | **1,327** | 1.0× |
| per day (15 active days) | 249 | 5.3× |
| per week (3) | 85 | 15.6× |
| once at phase end | **48** | **27.6×** |

- The gate fires on **214 of 400 commits (54%)**.
- One claim, `every-entry-prices-through-pricing-for`, is surfaced by **110** of
  those commits.
- Concentration is the mechanism: **40 of 82** cited paths back more than one
  claim, and `pricing/lease.rs` is **4,110 lines backing 10 claims**, so one
  edit anywhere in it surfaces ten claims — repeatedly, all phase long.

**1,279 of the 1,327 surfacings are re-reports of something already known and
still true at phase end.** That is the waste, and it is paid in re-checking
effort, not CPU: the gate itself runs in ~0.5 s.

## 2. The mechanism is already phase-scoped; the doctrine is not

`check --changed <base>` takes the **merge base** of `<base>` and `HEAD`. Aimed
at `origin/main` once, it reports 48 claims; aimed at `HEAD` before each commit,
1,327. Same command, same code — the whole 27.6× is the trigger.

So this is a doctrine defect, not an architecture one. Nothing in the tool needs
to change. Notably, no hook gates on commit today: `hooks.post_tool_use` only
calls `head_check`, which reports drift that arrived *because HEAD moved* (a
pull, a rebase) and runs no evaluation.

## 3. Design

### 3.1 The cadence

Replace "before committing, once" with **once per phase of work**, and name the
moments rather than leaving it to taste:

- `claimlock affected <paths>` while editing — no git, no gate, unchanged.
- `claimlock check --changed <base>` **when you are about to hand the work
  back**: opening a PR, reporting a phase or task complete, or claiming
  something is done or verified.
- CI keeps the gate on the pull request, which already runs once per push.

### 3.2 The stated trigger, not discretion

"Use your judgement" is not a rule an agent can evaluate. The trigger is the
same moment `verification-before-completion` already fires on — *about to
claim done* — which makes it checkable: if you are writing "complete", "fixed",
"verified" or opening a PR, the gate runs first; otherwise it does not.

A pre-commit hook is explicitly **discouraged** in the skills, with the number
attached, because it is the 27.6× trap and it reads like diligence.

### 3.3 What this does NOT fix

Stated plainly rather than implied: batching to phase end removes the *repeats*,
not the *work*. 48 claims are genuinely stale at phase end and each still needs a
real re-check. The only lever on that number is narrower pins — region pins
(`claimlock:begin/end`), which the measured store uses for **0 of 49** claims.
Cadence buys back the 1,279 redundant surfacings; regions are what would reduce
the 48.

## 4. Boogy's store: a dependency, not a task in this spec

Region-pinning the six hot files cannot be done on that repository as it stands:

- It runs `scripts/truth`, gated twice inside `scripts/verify.sh` (its own
  self-test, then `check`), and that tool has **no region support**.
- Its staleness model is **timestamp**-granular (`verified_at` against commit
  time), not content-hash pins — a different model, whose same-day ambiguity was
  itself a fixed defect (friction-log T-017).
- `claimlock import` deliberately lands every claim **unpinned**: an imported
  `verified` claim fails `check` until re-checked, because "an import must not
  launder old verifications into fresh pins". Migrating 49 claims therefore
  starts by failing all 49 — the same pile-up this spec exists to reduce,
  concentrated into one step.
- The hot files are under active development on another branch (`lease.rs` last
  committed hours before this measurement), so marker insertion would land in
  someone else's working area.

Recorded so the cost is visible. The migration is its own spec and its own
decision.

## 5. Non-goals

Changing any command, exit code, hook or default; adding a pre-commit hook;
gating on commit; migrating another repository's store; adding region markers to
files this repository does not own.
