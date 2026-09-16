# claimlock — orphan claims, evidence resolution, and using claimlock on claimlock

Status: approved 2026-09-16 (direction approved by the user; scope narrowed
twice by measurement before writing — see §2). Base specs:
`2026-09-14-claimlock-design.md`, `2026-09-14-claimlock-teams-design.md`,
`2026-09-15-claimlock-regions-renames-design.md`,
`2026-09-15-claimlock-output-budget-design.md`,
`2026-09-16-claimlock-edit-notice-design.md`,
`2026-09-16-claimlock-search-ranking-design.md`.

## 1. Problem, measured

Measured 2026-09-16 against a real 41-claim store and the 2,375-file repository
it documents.

| Question | Measured |
|---|---:|
| Claims no prose cites anywhere in the repo | **23 of 41** |
| `kind: test` evidence refs that resolve to a real test | **124 of 124** |
| Guarantee-shaped prose lines in living docs | **1,004** |
| Distinct `Claim:` markers in the whole repo | 20 |
| `claimlock check` baseline | **0.096 s** |
| One full source scan (1,019 files, 18.7 MB, stdlib Python) | **0.88 s** |

**The finding: 23 of 41 guarantees are cited by no prose at all.** `refs`
already fails on a marker naming no claim — the *dangling* direction. Nothing
looks the other way. A claim nobody cites is a guarantee nobody reads: it is
maintained, re-verified and gated on, while the prose it was written to support
never points at it. That is more than half this store.

Evidence citations are the mirror case and are, today, **clean**: every one of
124 `kind: test` refs resolves (124 checks — one per `kind: test` entry, not
deduplicated by test name; an earlier count of 121 counted distinct names
instead of entries and undercounted for that reason). The mechanism that
would break them — a renamed or deleted test, leaving a `verified` claim
citing a proof that no longer exists, with no state change and no gate
firing — is real and undetectable, but it has **no current instances**. This
work therefore treats it as prevention, not repair, and prices it accordingly
(§3.2).

## 2. What measurement removed, and why

Both were proposed, and both were dropped on evidence before implementation.
Recorded here so neither is re-proposed from intuition.

- **Executing evidence (`check --prove`).** The store's 4 `kind: run` refs are
  not commands. They are narrated procedures — *"grep for `prost`/`prost_types`
  under crates/…/src — the only non-test decode is `grpc_provision.rs::resolve`"*.
  There is nothing to execute. An executor would have been built for a data
  shape that does not exist, and would have introduced arbitrary code execution
  from files that arrive by `git pull`.
- **A "guarantee-shaped prose with no claim" detector.** 1,004 lines in 167
  living doc files match a modal-guarantee pattern; 11 carry a marker. The
  detector would emit ~993 findings against a 41-claim store — a firehose that
  teaches its reader to ignore it.

## 3. Design

### 3.1 Orphan claims — `refs`, inverted

`cmd_refs` already computes both sets it needs: the claim ids, and every marker
found by `refs.scan`. Orphans are one set difference on data already in hand:

```python
orphans = sorted(ids - {m.id for m in markers})
```

- `refs` **always** prints the orphan count in its census line. One number, no
  new scan, no measurable cost.
- `refs --orphans` lists them, one id per line, capped like every other listing
  (`LISTED_CLAIMS`, `--full` to see all).
- **Orphans never fail the gate.** `refs` keeps exiting 1 for dangling markers
  only. 23 of 41 on the real store means a failing default would be a migration
  cliff, and an uncited claim is a documentation gap, not a false statement.

All citations in the measured repo live in `.md` files, and **zero** claims are
cited only outside markdown, so the default `marker_globs` (`**/*.md`) produces
no false orphans. A project citing claims from source comments widens its own
`marker_globs`; that key already exists.

### 3.2 Evidence resolution — `claimlock evidence`

A `kind: test` ref names a test. If that test is renamed or deleted, the claim
stays `verified` forever, because nothing about its sources moved.

**Its own command, never a flag on `check`.** The scan costs 0.88 s against a
0.096 s baseline — 9× — and `check` is the gate that runs in hooks and CI. The
cost belongs where it is opted into.

- Resolves **`kind: test` only.** `measurement`, `source` and `run` refs are
  prose by design (§2) and are never resolved, never reported.
- **The locator rule**: take the longest identifier-shaped token in the ref
  (`[A-Za-z_][A-Za-z0-9_]*`, length ≥ 8). This handles both shapes present in
  the real store — `crate::module::the_test_name`, and the one prose ref naming
  two tests — with one rule and no per-language parsing.
- A ref with no token ≥ 8 characters is reported `UNLOCATABLE` and does **not**
  fail: it is a citation claimlock cannot check, which is a different fact from
  a citation that is wrong.
- A ref whose locator appears in no scanned file is `UNRESOLVED` and exits 1.
- Scanned files come from `refs.scan` with a new `globs` argument, defaulting to
  a new config key `evidence_globs` (default `["**/*"]`). The existing scanner
  already skips hidden directories, `node_modules`, the claims directory,
  gitignored files and anything that fails to decode as UTF-8.

`refs.scan(project, only=None, globs=None)` gains the parameter; `globs=None`
keeps today's `project.marker_globs` behaviour, so `refs` is unchanged.

### 3.3 claimlock uses claimlock

`claims/` is empty. The tool that exists to pin claims to code makes none about
itself, and on 2026-09-16 it shipped a defect that violated its own documented
contract: a braced query made `search` exit **1** — the code reserved for "no
match" — on a search that had found results.

- `claimlock init` in this repo, and a first set of claims covering contracts
  the suite already proves: hooks always exit 0; no git subprocess runs on the
  `post-edit` path; `search` exits 1 only when there is no match; `check` exits
  2 when the store cannot be read.
- Each cites its real sources and real evidence (`tests/…::test_name`), so
  `claimlock evidence` resolves against this repo's own tests.
- A test runs `claimlock check` **and** `claimlock evidence` against this
  repo's own store, so a self-claim going stale fails the suite. This is the
  gate, not the claims themselves — a claim store nothing checks is decoration.

## 4. Testing

- Orphans: a store with a cited and an uncited claim reports exactly one orphan;
  `--orphans` lists it; the census counts it; **exit stays 0** with orphans and
  no dangling markers; a dangling marker still exits 1.
- Locator rule: `crate::mod::a_long_test_name` → that name; the two-test prose
  ref → one of the two; `kind: measurement` prose → never consulted; a ref whose
  longest token is `abc` → `UNLOCATABLE`, exit 0.
- Resolution: a claim citing a test that exists resolves; renaming that test in
  the fixture makes the same claim `UNRESOLVED` with exit 1. **The rename is the
  falsifier** — without it the test passes on a scanner that always returns true.
- Cost: `refs` gains no measurable time (assert the orphan path issues no
  additional file read); `evidence` reports its scanned-file count.
- Self-store: `check` and `evidence` both pass on this repo's own claims.

## 5. Cost

`refs` unchanged (a set difference on loaded data). `evidence` is one full scan,
measured at 0.88 s over 18.7 MB of source in stdlib Python, reported in its
census line. If it grows past a few seconds the answer is narrower
`evidence_globs`, **not** a persisted index — the staleness surface the base
design rejects.

## 6. Non-goals

Executing evidence; a guarantee-prose heuristic (both §2, with the measurements
that killed them); resolving `measurement`/`source`/`run` refs; failing the gate
on orphans; any new persisted state, cache or network call; changing what
`check` costs.
