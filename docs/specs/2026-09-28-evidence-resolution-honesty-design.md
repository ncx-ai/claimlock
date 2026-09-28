# Evidence resolution: make `resolved` mean something — design

**Status:** proposed, 2026-09-28. Written from a measured adoption of claimlock
against a 140-claim store (549 `kind: test` refs), which is 3.4× the 41-claim
store the current rule was designed against.

## 1. The finding

`claimlock evidence` reports a ref as `resolved` when nothing about that ref was
checked. Measured on the 140-claim store:

| ref | its locator | files containing that token |
|---|---|---|
| `shell.test.ts: 'attributes a frame to the pane that sent it, not to its origin-mate'` | `attributes` | **79** |
| `pane.test.ts: 'ignores a connect from an origin the platform does not name'` | `platform` | **1,001** |
| `boogy-pricing::ledger::ops::tests::flush_debits_conserves_after_the_commit_across_different_shards` | the whole test name | 1 |
| `boogy-host::bootstrap_postgres_tests::no_url_refuses` | `bootstrap_postgres_tests` | 2 |

`resolved` for the first two means *"the English word `platform` appears
somewhere in 1,001 files"*. Both report identically to the third, which is a real
check.

Verified by mutation rather than by reading: breaking the quoted test name to
`'this vitest name does not exist anywhere at all'` left the census byte-identical
at `543 resolved, 6 unresolved`. Breaking the **filename** to
`no-such-file.test.ts` also left it identical — because the filename was never
what was checked.

Row four is the same defect in a form that looks like a success: the locator is
the *module* name, so `no_url_refuses` — the test the ref actually names — is
never looked for. That is why this command cannot see a ref whose cargo
invocation does not work: `cargo test -p boogy-host --lib bootstrap_postgres_tests`
runs **zero** tests and exits 0, because those tests live in `main.rs`, a binary,
and need `--bins`. Three separate refs in that store could not be run as written;
`evidence` reported all three `resolved`.

## 2. Why the current rule is not a mistake

The rule is stated in
`docs/specs/2026-09-16-claimlock-orphans-evidence-design.md` §3.2:

> **The locator rule**: take the longest identifier-shaped token in the ref
> (`[A-Za-z_][A-Za-z0-9_]*`, length ≥ 8). This handles both shapes present in the
> real store — `crate::module::the_test_name`, and the one prose ref naming two
> tests — with one rule and no per-language parsing.

That was true when written. The store had 41 claims and **one** prose ref, and a
single rule covering both shapes was the right trade against per-language
parsing. What changed is the population: 549 test refs, of which **11** are prose
refs naming a file and a quoted test name. The rule's own stated basis — "both
shapes present in the real store" — no longer describes the store.

So this design does not overturn a bad decision. It reports that the measurement
the decision rested on has moved, and it keeps the part that still works
(`crate::module::test_name` resolves exactly, by its own long unique token).

## 3. The governing rule

From claimlock's own `evidence-standards` skill:

> "Found nothing" and "cannot see anything" must never print the same.

`unlocatable` already exists for exactly this, and its docstring already says it
is *"a citation claimlock cannot check, which is a different fact from one that is
wrong."* The defect is that a common-word locator never reaches that state — it
reaches `resolved`.

## 4. Design

### 4.1 A locator must be able to discriminate

A token that appears in a large fraction of the repository establishes nothing. A
ref whose only locator is such a token is **`unlocatable`**, not `resolved`.

Discrimination is decided against the scan claimlock already performs, so it
costs no second pass: while scanning, count how many **files** each needed
locator appeared in. After the scan:

- appeared in exactly 1 file → `resolved`
- appeared in 0 files → `unresolved` (unchanged)
- appeared in more than `AMBIGUOUS_FILES` files → **`unlocatable`**, reported as
  ambiguous with the count

`AMBIGUOUS_FILES = 3`. Rationale, from the measured store: a genuine Rust test
name appears in 1 file (its definition) or 2 (definition plus one citation);
`no_url_refuses` appears in 2, `bootstrap_postgres_tests` in 2. The lowest
common-word locator measured is `attributes` at 79. Nothing real sits between 3
and 79, so the threshold is not tuned to a boundary case — any value in that gap
separates the two populations, and 3 is chosen as the smallest that admits every
observed genuine ref.

**This alone converts a silent false pass into a visible "I cannot check this",**
and it is worth shipping even if nothing else here is built.

### 4.2 An explicit ref shape, resolved exactly

A ref may name its test unambiguously:

```
<repo-relative file>::<test name>
```

- **Rust**: `crates/boogy-host/src/main.rs::bootstrap_postgres_tests::no_url_refuses`
- **vitest**: `packages/web-sdk/src/shell.test.ts::createShell > attributes a frame to the pane that sent it, not to its origin-mate`
- **node:test**: `apps/boards/tests/pane-link.test.ts::a pane at the root of its site never follows a saved location to another host`

The shape is one family with the existing `::` convention, and it is what a
person would type to run the test. It was proposed by the engineer who owns the
JS side of the measured store rather than invented here.

Resolution for such a ref:

1. The file must exist at that repo-relative path. If not → `unresolved`, naming
   the path. **A bare filename with no directory is refused** (`unlocatable`):
   two packages may hold `shell.test.ts`.
2. The remainder must name a test in that file.

### 4.3 Three resolution strengths, named separately

| state | meaning |
|---|---|
| `resolved` | the runner itself lists this test at this file |
| `matched` | the file exists and the test's name appears in it as a literal, but no runner was asked |
| `unresolved` | the file or the name is absent |
| `unlocatable` | claimlock cannot check this ref at all |

`matched` is new and is the load-bearing addition. Without it, a static check and
a real one print the same, which is the defect of §1 in a weaker form — and a
static check genuinely cannot see a test name built in a loop, which the measured
store contains (`for (const [error, words] of …) it(\`${error}: …\`)`).

### 4.4 How a runner is asked, and when

**Never from `check`, and never by default.** The existing constraint holds:
`check` is the gate that runs in hooks and CI, its baseline is 0.096 s, and one
evidence scan costs 0.88 s. Running a test runner is far more expensive again.

`claimlock evidence` stays static by default and reports `matched` for §4.2 refs.
`claimlock evidence --ask-runners` additionally consults a runner per language:

- **vitest**: from the nearest ancestor `package.json` whose dependencies include
  vitest, run `vitest list --json <out>` and read `{name, file}` pairs.
  `name` is the describe chain joined by ` > ` **without** the file; `file` is
  **absolute**, so it must be relativised against the repo root before
  comparing. (The pretty `vitest list` output is a *different* shape — `file >
  describe > test` on one line — and must not be parsed instead.) Measured: 410
  tests, 90 KB of JSON, on the store this was designed against.
- **cargo**: `cargo test -p <pkg> --all-targets -- --list`, which names the
  target kind, so a ref naming a lib test that is really a bin test is
  `unresolved` with the correct invocation in the message.
- **node:test**: no list mode exists. Stays `matched`, never `resolved`.

**Safety.** A claim file arrives by `git pull` and is untrusted. The existing
design refused an executor for that reason (§2 of the 2026-09-16 spec) and that
refusal stands for `ref` CONTENT: nothing from a ref is ever passed to a shell,
interpolated into a command, or used as a path outside the repo. What
`--ask-runners` runs is a fixed argv per language, discovered from the
repository's own manifests, never from a claim. The flag is opt-in so a store that
does not want any subprocess never gets one.

**`--json <path>` is never accepted from a ref, and claimlock's own runner
invocation writes its report under a temporary directory it creates.** Recorded
because the measured cost of getting this wrong was real: during this
investigation `npx vitest list --json src/shell.test.ts` was read by vitest as
"write the report TO that path" and truncated a 251-line test file.

### 4.5 Migration

Every existing ref keeps working. §4.1 can move a ref from `resolved` to
`unlocatable`, which does **not** fail the gate — by the same reasoning orphans
do not: a store adopting this must not meet a cliff. `unresolved` remains the only
failing state.

The census line gains the new counts, so a store can see what it has:

```
claimlock: 549 refs — 538 resolved, 0 matched, 6 unresolved, 5 unlocatable (5 ambiguous) in 2730 files scanned
```

## 5. What this does not do

- It does not verify a test **passes**. It verifies the citation names a real
  test. A claim asserting a passing test is what `kind: run` refs are for, and
  those stay prose.
- It does not resolve `measurement`, `source` or `run` refs. Unchanged, and for
  the reason the 2026-09-16 spec gives: they are prose, and two of the measured
  store's claims rest on observations no suite can re-make (a minor-fault count,
  a benchmark).
- It does not make `evidence` part of `check`.
- It does not address the three other findings from the same adoption (no honest
  state for adopting an existing store; `verify` silently dropping `verified_at`
  and crashing a predecessor tool mid-migration; `import`'s undocumented
  argument). Those are their own change.
