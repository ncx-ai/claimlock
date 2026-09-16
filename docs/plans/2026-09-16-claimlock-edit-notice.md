# claimlock edit-time notice and cleanup — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell Claude which claims cite a file at the moment it edits that file, and clear five verified-live cleanup items.

**Architecture:** One new hook event (`post-edit`) in `lib/claimlock/hooks.py`, wired as a second PostToolUse entry in `hooks/hooks.json`. It reads the payload's file paths, maps them to citing claims through an index cached in the existing session-state file, and prints one `additionalContext` message. No hashing, no git. The cleanup is five small, independent edits.

**Tech Stack:** Python ≥ 3.11 standard library, `unittest`.

**Spec:** `docs/specs/2026-09-16-claimlock-edit-notice-design.md` — read all of it before either task. Base specs: the three earlier design docs it names.

## Global Constraints

- Python ≥ 3.11 standard library only in `lib/`; `bin/claimlock` is touched only by Task 2's launcher item, and must keep parsing on Python 3.6+.
- Tests: `python3 -m unittest discover -s tests` from the repo root must pass — **378** at the start of this plan; `python3 bin/claimlock self-test` must stay at 19/19.
- **Every hook exits 0**, never sets `decision`, prints nothing in a project with no store, and logs internal errors to `hook-errors.log`. Output ≤ 2,000 characters.
- **`post-edit` spawns no git subprocess and hashes nothing.** The existing PostToolUse no-git guarantee must also hold for the new event, pinned by a test using the fake-git-on-PATH pattern already in `tests/test_hooks.py`.
- Silence is the common case: no store, no usable path, a path outside the root, a path inside the claims directory, or no `verified`/`owed` claim citing it ⇒ print nothing.
- Exit codes, verdicts, counts and the summary line of every CLI command are unchanged by this plan.
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
| `lib/claimlock/hooks.py` | `post_edit` handler, payload path extraction, cited-claims index + signature, suppression, `HANDLERS` entry |
| `hooks/hooks.json` | second PostToolUse entry, matcher `Edit\|Write\|MultiEdit\|NotebookEdit` |
| `tests/test_edit_notice.py` | NEW (Task 1) |
| `README.md`, `docs/format.md`, `skills/using-claimlock/SKILL.md` | Task 1 docs: the new hook in the Hooks table, its message shape, one skill line |
| `lib/claimlock/hashing.py` | NEW (Task 2): the single blob-hash home |
| `lib/claimlock/pins.py`, `lib/claimlock/regions.py` | Task 2: import the shared hash |
| `lib/claimlock/cli.py` | Task 2: sort `elsewhere`, one rename-format helper |
| `bin/claimlock` | Task 2: cap the old-Python log |
| `tests/test_hooks.py`, `tests/test_output_budget.py` | Task 2: tests for the log cap, the `_log` guard, the `elsewhere` sort |

## Shared interfaces (exact names)

```python
# hooks.py
def post_edit(project, payload, data_dir) -> dict | None      # HANDLERS["post-edit"]
def _edited_paths(project, payload) -> list[str]              # root-relative, inside root, outside claims_dir, deduped, order preserved
def _cited_index(project, st) -> dict[str, list[str]]         # {source path: [claim id, ...]} for verified/owed claims only; cached in st
def _claims_signature(project) -> str                         # digest of (name, size, mtime_ns) for claims_dir/*.md

# hashing.py  (Task 2)
def blob_of_bytes(data: bytes) -> str                         # moved verbatim from pins.py; pins and regions both import it
```

---

### Task 1: The `post-edit` hook

**Files:**
- Modify: `lib/claimlock/hooks.py`, `hooks/hooks.json`, `README.md`, `docs/format.md`, `skills/using-claimlock/SKILL.md`
- Create: `tests/test_edit_notice.py`

**Interfaces:** Produces `post_edit`, `_edited_paths`, `_cited_index`, `_claims_signature`. Consumes the existing `main()` dispatch, `_session_lock`, `_load_state`/`_save_state`, `_state_path`, `LIMIT`, and `claims.load_claims`.

Behaviour: spec §3 in full.

- [ ] **Step 1: Write the failing tests** in `tests/test_edit_notice.py`, following `tests/test_hooks.py`'s `HookCase` style (`self.hook(root, "post-edit", stdin=<payload json>)`):
  - **Silence** (each asserts the hook returns `None`): no store; `tool_input` absent; `file_path` not a string; a path outside the project root; a path inside `claims/`; a cited path whose only citing claim is `unverified`; a path no claim cites.
  - **Names the claims**: a store where `a.py` is cited by a `verified` claim `c1` and an `owed` claim `c2` — the message contains `a.py`, `c1`, `c2`, the words `verified` and `owed`, `claimlock diff`, and `claimlock verify`; it is `hookSpecificOutput.additionalContext` with `hookEventName` `PostToolUse`.
  - **Payload shapes**: `{"tool_input": {"file_path": "<abs>"}}` and a relative `file_path` both resolve; `{"tool_input": {"edits": [{"file_path": "<abs>"}, ...]}}` collects each; `{"tool_input": {"notebook_path": "<abs>"}}` resolves.
  - **Suppression**: the same path twice in one session — second call returns `None`; a *different* cited path in the same session still speaks; the same path in a different `session_id` speaks.
  - **Index freshness**: after the first call, editing a claim file's content to add a new source (same file count, content change) makes the next call see the new citation — the signature must catch an in-place edit, which a directory mtime would miss.
  - **Bounds**: 9 citing claims ⇒ at most 5 named plus `…`; a message with many long ids is ≤ 2,000 characters.
  - **No git**: reuse `tests/test_hooks.py`'s fake-git-on-PATH assertion for a `post-edit` call, cited and uncited.
  - **Region source**: a claim citing `a.py` with a `region` is named when `a.py` is edited (the hook does not hash, so region membership is not consulted).
- [ ] **Step 2: Run** `cd tests && python3 -m unittest test_edit_notice -v` — expect failures naming the missing event.
- [ ] **Step 3: Implement** per spec §3: `_edited_paths`, `_claims_signature`, `_cited_index` (cached in session state under `cited` + `cited_signature`), `post_edit` (suppression under `edited_notified`), the `HANDLERS` entry, and the `hooks.json` matcher. Keep `main()`'s structure: the session lock, the store check and the error handling already wrap every event.
- [ ] **Step 4: Run** focused tests, then the full suite and `python3 bin/claimlock self-test`.
- [ ] **Step 5: Docs.** README's Hooks table gains a row for the new hook (who sees it, when) and the Hooks prose mentions per-session suppression; `docs/format.md`'s "Hook messages" section documents the message shape, the caps and the silence rules; `skills/using-claimlock/SKILL.md` gains one line under its hook-messages guidance saying what the notice means and what to do (re-check before verifying, never re-stamp).
- [ ] **Step 6: Commit** `feat(hooks): name the claims a file backs at edit time`.

### Task 2: The cleanup pass

**Files:**
- Create: `lib/claimlock/hashing.py`
- Modify: `lib/claimlock/pins.py`, `lib/claimlock/regions.py`, `lib/claimlock/cli.py`, `lib/claimlock/hooks.py`, `bin/claimlock`
- Test: `tests/test_hooks.py`, `tests/test_output_budget.py`

**Interfaces:** Produces `hashing.blob_of_bytes`. Consumes Task 1's `hooks.py` (both tasks touch it — Task 2 runs after Task 1 is committed).

Behaviour: spec §4. Five independent fixes; each keeps today's observable behaviour except where a test says otherwise.

- [ ] **Step 1: `_log` must not raise.** `hooks.py::_append_log` catches `OSError`, but `_log_dir` calls `Path.home()`, which raises `RuntimeError` when HOME is unset and the user has no passwd entry — escaping the handler whose whole job is to swallow errors. Catch `Exception`. Test: with `HOME` unset and `Path.home` patched to raise `RuntimeError`, `hooks._log(None, "stop")` and `_log_note(None, "x")` return without raising.
- [ ] **Step 2: Cap the launcher's log.** `bin/claimlock` appends a line to `hook-errors.log` on every hook invocation under Python < 3.11, unbounded. Skip the append when the file is already over 1 MiB (stat, then write). Keep the file parsing on Python 3.6. Test: a pre-filled oversized log is not appended to; a small one is.
- [ ] **Step 3: Sort the pre-existing list.** `cli.py::cmd_check` sorts `blocking` problems-first; `elsewhere` (the `pre-existing (not changed here):` list) is unsorted, so an invalid claim there can fall past `LISTED_CLAIMS` unnamed. Apply the same stable sort. Test: 22 pre-existing stale claims plus one invalid sorting last ⇒ the invalid id appears in the default `check --changed` output.
- [ ] **Step 4: One rename-format helper.** `cli.py` formats a rename (`renamed → <new> (<sha>)`) at three sites (`_print_failing`, the JSON source entries, `cmd_diff`). Hoist one helper; output must stay byte-identical — the existing rename tests pin it.
- [ ] **Step 5: One blob-hash home.** `regions.py::region_hash` duplicates `pins.py::blob_of_bytes` because `pins` imports `regions` at module scope. Create `lib/claimlock/hashing.py` holding `blob_of_bytes` verbatim; have both `pins.py` and `regions.py` import it; keep `pins.blob_of_bytes` as a re-export so existing imports (tests included) keep working. No behaviour change: the existing hashing tests must pass untouched.
- [ ] **Step 6: Run** the full suite and `python3 bin/claimlock self-test`; commit `fix: cleanup pass — log guards, pre-existing sort, shared helpers`.
