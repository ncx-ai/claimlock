# claimlock orphans + evidence resolution — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report the claims no prose cites (23 of 41 on the real store), let `claimlock evidence` prove that every `kind: test` ref still names a real test, and make claimlock keep claims about itself — gated by its own suite.

**Architecture:** `refs.py` already walks every candidate file once and returns the markers it finds; orphans are that result inverted, a set difference on data `cmd_refs` already holds. A new pure module `evidence.py` turns an evidence `ref` into a locator and audits a claim set against one scan. Nothing is persisted, cached, or executed.

**Tech Stack:** Python ≥ 3.11 standard library, `unittest`.

**Spec:** `docs/specs/2026-09-16-claimlock-orphans-evidence-design.md` — read all of it, especially §2 (what measurement removed, so it is not re-added), §3.1 (orphans never fail the gate) and §3.2 (the locator rule, and why `evidence` is its own command).

**Base:** `4c75fad`.

## Global Constraints

- Python ≥ 3.11 standard library only. **No new dependency, no network call, no persisted index, no cache file.**
- Tests: `python3 -m unittest discover -s tests` from the repo root — **462** at the start of this plan; `python3 bin/claimlock self-test` stays at **19/19**.
- **`refs`'s existing contract is unchanged**: it exits 1 for dangling markers and 0 otherwise. Orphans are reported and never change an exit code.
- **Only `kind: test` refs are ever resolved.** `measurement`, `source` and `run` refs are prose by design (spec §2) — never parsed, never reported.
- `check` must not get slower: its baseline is 0.096 s and one scan costs 0.88 s. No scan may be added to `check`, `_evaluate`, or any hook.
- The README must list every command in `claimlock --help` (`tests/test_plugin_manifest.py`); skills obey `tests/test_skills_format.py`, whose `FORBIDDEN` regex bars project-specific words.
- Every new listing is bounded like its neighbours (`LISTED_CLAIMS = 20`, `--full` to see all) per the output-budget spec.
- Revert temporary mutations by re-applying the inverse edit, never `git checkout --`. Stage explicit paths, never `git add -A`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv
  ```

## File map

| File | Change |
|---|---|
| `lib/claimlock/refs.py` | Task 1: `files(...)` made public and given a `globs` parameter |
| `lib/claimlock/cli.py` | Task 1: `cmd_refs` reports orphans, `--orphans`/`--full`. Task 2: `cmd_evidence` |
| `lib/claimlock/evidence.py` | NEW (Task 2): `locator`, `audit` — pure, no I/O of its own |
| `lib/claimlock/project.py` | Task 2: `evidence_globs` in `DEFAULTS` + validation |
| `tests/test_refs_import_selftest.py` | Task 1: orphan cases |
| `tests/test_evidence.py` | NEW (Task 2) |
| `tests/test_self_store.py` | NEW (Task 3): claimlock's own store must pass |
| `claims/*.md`, `.claimlock.toml` | Task 3 |
| `README.md`, `docs/format.md` | Tasks 1–3 |

## Shared interfaces (exact names)

```python
# refs.py — `files` is today's private `_files`, plus `globs`
def files(project, only=None, globs=None)      # globs=None keeps project.marker_globs
def scan(project, only=None)                   # behaviour unchanged

# evidence.py — pure
MIN_LOCATOR = 8
def locator(ref: str) -> str | None            # longest [A-Za-z_][A-Za-z0-9_]* of len >= MIN_LOCATOR

@dataclass
class Audit:
    resolved: list[tuple[str, str]]            # (claim id, locator)
    unresolved: list[tuple[str, str]]          # (claim id, ref)
    unlocatable: list[tuple[str, str]]         # (claim id, ref)
    scanned: int

def audit(project, claims, globs=None) -> Audit
```

---

### Task 1: Orphan claims

**Files:** Modify `lib/claimlock/refs.py`, `lib/claimlock/cli.py`, `tests/test_refs_import_selftest.py`, `README.md`, `docs/format.md`

**Interfaces:** Produces `refs.files(project, only=None, globs=None)`. Consumes nothing new.

- [ ] **Step 1: Write the failing tests** in `tests/test_refs_import_selftest.py`. Build a store with two claims, prose citing only the first:
  - `refs` census line ends `, 1 uncited` and **exit 0** (no dangling markers).
  - `refs --orphans` prints `UNCITED <id>` for the uncited claim only.
  - A dangling marker still exits 1, and the census still reports both numbers.
  - 25 uncited claims with `--orphans` print 20 then the cut note; `--orphans --full` prints all 25.
- [ ] **Step 2: Run** `cd tests && python3 -m unittest test_refs_import_selftest -v` — expect failures on the census wording and the unknown `--orphans` flag.
- [ ] **Step 3: Implement.** Rename `refs._files` to `refs.files` with a third parameter (keep behaviour identical when `globs is None`):

```python
def files(project, only=None, globs=None):
    globs = project.marker_globs if globs is None else globs
```

Replace every `_matches(rel, project.marker_globs)` inside it with `_matches(rel, globs)`, and update `scan` to call `files(project, only)`. Then in `cli.py`:

```python
def cmd_refs(args):
    project = _project(args)
    ids = {c.id for c in C.load_claims(project)}
    markers, scanned = refs.scan(project)
    dangling = [m for m in markers if m.id not in ids]
    orphans = sorted(ids - {m.id for m in markers})
    for m in dangling:
        print(f"DANGLING {m.path}:{m.line}  Claim `{m.id}` names no claim")
    if args.orphans:
        _print_capped(orphans, None if args.full else LISTED_CLAIMS,
                      "… and {n} more uncited claims — claimlock refs --orphans --full",
                      lambda cid: print(f"UNCITED {cid}"))
    print(f"claimlock: {len(markers)} markers in {scanned} files scanned, "
          f"{len(dangling)} dangling, {len(orphans)} uncited")
    return 1 if dangling else 0
```

Register the flags where `refs` is added: `p = add("refs", cmd_refs, "fail on Claim markers that name no claim; report claims no prose cites")`, then `p.add_argument("--orphans", action="store_true")` and `p.add_argument("--full", action="store_true")`.

- [ ] **Step 4: Run** the focused tests, then the full suite.
- [ ] **Step 5: Docs.** README's `claimlock refs` row gains the orphan report and says plainly that an uncited claim never fails the gate; `docs/format.md`'s refs section gains the census wording and `--orphans`.
- [ ] **Step 6: Commit** `feat(refs): report claims no prose cites`.

### Task 2: `claimlock evidence`

**Files:** Create `lib/claimlock/evidence.py`, `tests/test_evidence.py`; modify `lib/claimlock/project.py`, `lib/claimlock/cli.py`, `README.md`, `docs/format.md`

**Interfaces:** Consumes Task 1's `refs.files(..., globs=...)`. Produces `evidence.locator` / `evidence.audit`.

- [ ] **Step 1: Write the failing tests** in `tests/test_evidence.py`:
  - `locator("boogy-host::grpc_edge::tests::a_detail_beyond_the_cap_is_dropped")` → `"a_detail_beyond_the_cap_is_dropped"`; `locator("pkg: one_long_test_name (both), another_long_test_name")` returns one of the two long names; `locator("abc::xy")` → `None`.
  - `audit`: a claim citing a test that exists in a scanned file is `resolved`; **rename that test in the fixture and the same claim becomes `unresolved`** (this rename is the falsifier — without it the test passes on a scanner that always returns true);
  - a `kind: measurement` prose ref is never consulted, even when its words appear nowhere;
  - a `kind: test` ref whose longest token is 3 characters is `unlocatable` and does not fail.
  - CLI: exit 1 when anything is unresolved, exit 0 when only `unlocatable` entries remain; the census names the scanned-file count; `--full` uncaps the listings.
- [ ] **Step 2: Run** `cd tests && python3 -m unittest test_evidence -v` — expect an import error for the missing module.
- [ ] **Step 3: Implement `evidence.py`** per spec §3.2. Keep it pure — it takes `project` and claims and calls `refs.files`; it must not re-implement file walking, and must skip files that fail to decode (`refs` already does):

```python
LOCATOR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIN_LOCATOR = 8

def locator(ref):
    return max((t for t in LOCATOR_RE.findall(ref) if len(t) >= MIN_LOCATOR),
               key=len, default=None)
```

`audit` collects every `kind: test` ref's locator into one dict, walks `refs.files(project, None, globs)` **once**, and records which locators were seen. Ties in `max` take the first-longest, which is deterministic for a given ref.

- [ ] **Step 4: Config.** Add `"evidence_globs": ["**/*"]` to `project.DEFAULTS` and validate it exactly as `marker_globs` is validated (a list of strings, else `ConfigError`). Add the field to `Project` and pass it through. A store with no config gets the default.
- [ ] **Step 5: Wire the command** in `cli.py` — `add("evidence", cmd_evidence, "resolve every kind: test evidence ref to a real test")` with `--full`; print `UNRESOLVED <id>  <ref>` and `UNLOCATABLE <id>  <ref>` lines, both capped at `LISTED_CLAIMS`, then a census line naming resolved/unresolved/unlocatable counts and files scanned. Return 1 only when `unresolved` is non-empty.
- [ ] **Step 6: Run** focused tests, the full suite, and `python3 bin/claimlock self-test`.
- [ ] **Step 7: Docs.** README's Commands table gains a `claimlock evidence` row (required by `tests/test_plugin_manifest.py`) stating that it resolves `kind: test` refs only and is deliberately not part of `check` because it costs a full scan; `docs/format.md` gains the locator rule and the three outcomes.
- [ ] **Step 8: Commit** `feat(evidence): resolve test evidence refs to real tests`.

### Task 3: claimlock keeps claims about claimlock

**Files:** Create `.claimlock.toml`, `claims/*.md`, `tests/test_self_store.py`; modify `README.md`

**Interfaces:** Consumes Tasks 1 and 2 (`refs`, `evidence`).

- [ ] **Step 1: Initialise the store.** Run `python3 bin/claimlock init` in the repo root. Keep the generated `.gitignore`/`.gitattributes` edits.
- [ ] **Step 2: Write the failing gate** in `tests/test_self_store.py`: run the CLI in the repo root and assert `claimlock check` exits 0, `claimlock evidence` exits 0, and `claimlock refs` exits 0. Use the existing `run_cli` helper from `tests/helpers.py` with the repo root as cwd. Expect failure now — there is no store.
- [ ] **Step 3: Write the claims.** At least four, each `status: verified`, each citing the real source files and **evidence refs naming tests that exist in this repo** (`tests/test_hooks.py::<class>::<test>` style; derive each from a test you have actually run — do not invent names, and confirm with `claimlock evidence` that each resolves):
  - hooks always exit 0, whatever the payload;
  - the `post-edit` path spawns no git subprocess;
  - `search` exits 1 only when there is no match (cite the brace regression test from `tests/test_output_budget.py`);
  - `check` exits 2 when the store cannot be read.

  Verify each with `python3 bin/claimlock verify <id>` so the pins and `pins:` digest are written by the tool, never by hand.
- [ ] **Step 4: Cite them.** Add a `` Claim: `<id>` `` marker beside the matching sentence in `README.md` or `docs/format.md` for each claim, so `refs --orphans` reports zero and the store demonstrates the discipline it sells.
- [ ] **Step 5: Run** the full suite plus `python3 bin/claimlock self-test`, then `claimlock check`, `claimlock evidence` and `claimlock refs --orphans` in the repo root; paste all three outputs into the report.
- [ ] **Step 6: Commit** `feat: claimlock keeps claims about claimlock`.
