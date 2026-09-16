# claimlock — the edit-time claim notice, and a cleanup pass

Status: approved 2026-09-16 (design presented and approved by the user). Base
specs: `2026-09-14-claimlock-design.md`, `2026-09-14-claimlock-teams-design.md`
(T1–T15), `2026-09-15-claimlock-regions-renames-design.md`,
`2026-09-15-claimlock-output-budget-design.md`.

## 1. Problem

claimlock reports drift **after** it happens. The hooks fire at session start,
after Bash and MCP calls (only when HEAD moved), and at end of turn. So when
Claude edits a file that a claim depends on, nothing says so at the moment of
the edit; the warning arrives after the commit, by which time the cheapest
response — re-read the claim while the change is fresh — has passed, and the
habit it invites is to re-stamp `verify` rather than re-check.

A notice at edit time turns a detector into a preventer. It is also the only
hook that can be silent almost always: most edited files back no claim.

## 2. Observed hook facts this rests on

- `Edit|Write` is a valid `hooks.json` matcher, and a PostToolUse payload for
  those tools carries `tool_input.file_path` (Claude Code documentation,
  checked 2026-09-16).
- A PostToolUse hook's `hookSpecificOutput.additionalContext` reaches the model
  — CONFIRMED with transcript evidence in `docs/hook-semantics.md` (F3), which
  is stronger evidence than the documentation, where it is not shown for this
  event.
- `MultiEdit` / `NotebookEdit` payload shapes are **not** confirmed. The design
  therefore reads defensively and stays silent when it finds no usable path.

## 3. The `post-edit` hook

A new hook event, `claimlock hook post-edit`, wired as a second PostToolUse
entry with matcher `Edit|Write|MultiEdit|NotebookEdit`. A matcher that names a
tool which does not exist simply never fires, so listing the unconfirmed two
costs nothing.

### 3.1 What it does

1. Collect candidate paths from the payload: `tool_input.file_path`,
   `tool_input.notebook_path`, and any `file_path` inside a list at
   `tool_input.edits`. Non-string values are ignored. No path → print nothing.
2. Resolve each against the project root; drop anything outside it, and drop
   anything inside the claims directory (editing a claim is not source drift).
3. Look up which claims cite those paths (§3.2). Only claims whose status is
   `verified` or `owed` count: those are the ones carrying pins that the edit
   may have invalidated. An `unverified` or `refuted` claim has nothing to
   drift, and naming it would be noise.
4. Nothing cited → print nothing. This is the common case and must stay cheap.
5. Otherwise print one `additionalContext` message (§3.3) and record the paths
   as notified (§3.4).

**No hashing and no git, ever.** The edit just happened, so a verified claim
citing the file is presumed drifted; computing freshness would add cost to
answer a question the notice does not ask. This preserves the existing
guarantee that a PostToolUse hook spawns no git subprocess.

### 3.2 The path → claims lookup

Built by loading the claim files (file reads only) and mapping each cited
`source.path` to the ids that cite it. A region source maps by its `path`, not
its key: an edit anywhere in the file is worth reporting even when the region
may be untouched, because the hook does not hash.

Cached in the session state file under `cited`, beside a `cited_signature`:
the digest of `(name, size, mtime_ns)` for every `*.md` in the claims
directory. The directory's own mtime is not enough — editing a claim's content
in place does not change it — so the signature stats each file. On a signature
mismatch the index is rebuilt. The same cache write also stores
`cited_status` (claim id → status), so the message (§3.3) can label a hit
without a second pass over the claims.

### 3.3 The message

```
claimlock: src/limit.py backs 2 claims — retries-are-capped (verified),
timeout-is-clamped (owed). Your edit may have invalidated them: re-check with
`claimlock diff <id>` before any `claimlock verify`.
```

- At most 5 claims named, then `…`; at most 3 paths named per message.
- Capped at 2,000 characters like every other hook message.
- `hookSpecificOutput.additionalContext`, so it reaches Claude, not the user.

### 3.4 Suppression

A path is named at most once per session, recorded in the session state under
`edited_notified`. Editing one file ten times says it once. The record is per
path, not per claim, and it is not re-armed when a claim's state changes —
end-of-turn and HEAD-moved reporting already cover what happened afterwards.

### 3.5 Contract

Exit 0 always; never sets `decision`; prints nothing in a project without a
store; errors go to `hook-errors.log`. Identical to the other three events.

## 4. Cleanup pass

Each verified live against the code at `0b2489f` (five other ledger entries
were found already fixed by later work and are dropped):

| Fix | Where | Shape |
|---|---|---|
| `_log`/`_log_note` can raise | `hooks.py::_append_log` catches only `OSError`, but `_log_dir`'s `Path.home()` raises `RuntimeError` when HOME is unset and the user has no passwd entry — escaping the very handler that exists to swallow errors | catch `Exception` |
| Launcher log grows unbounded | `bin/claimlock` appends a line per hook invocation on Python < 3.11, with no cap | skip the write when the file is over 1 MiB |
| Pre-existing list hides an invalid claim | `cli.py::cmd_check` sorts `blocking` problems-first but not `elsewhere`, so an invalid claim there can fall past `LISTED_CLAIMS` | same stable sort |
| Rename lookup repeated | `cli.py` formats a rename in three places | one helper |
| Two blob-hash implementations | `regions.py::region_hash` duplicates `pins.py::blob_of_bytes` (to break an import cycle) | move the hash into its own module both import |

Deliberately **not** in scope: the region-pin history walk (~24 ms per
evaluate, `claims.py:404`) — it needs a design change to the anchoring query,
not a tidy-up.

## 5. Testing

- Payload shapes: `Edit`, `Write`, a payload with no `file_path`, a list at
  `tool_input.edits`, a path outside the root, a path inside the claims
  directory — the last four print nothing.
- An uncited path prints nothing; a cited path names its claims; an
  `unverified`-only citation prints nothing.
- Suppression: the second edit of the same path in one session is silent.
- The index rebuilds when a claim file's content changes (signature test).
- **No git**: the existing fake-git-on-PATH assertion pattern in
  `tests/test_hooks.py`, applied to `post-edit`.
- Output ≤ 2,000 characters with many claims; at most 5 named.
- Cleanup items each get a test, except the two cosmetic ones (the rename
  helper and the shared hash), which are covered by the existing suite.

## 6. Risks

- **Noise.** If a project's claims cite broad paths, every edit could speak.
  Mitigated by per-session suppression, by naming only pinned claims, and by
  silence when nothing is cited. If it still proves noisy in real use, the next
  lever is to narrow what counts as a citation, not to raise the cap.
- **Cost on edit-heavy sessions.** The lookup is file reads plus one stat per
  claim file, cached per session. No git, no hashing.
- **Unknown payload shapes** for MultiEdit/NotebookEdit: silence, never a
  crash, and the tests pin that.
