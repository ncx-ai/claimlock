# claimlock for teams — design

Date: 2026-09-14. Status: approved in brainstorming, awaiting spec review.
Builds on: `docs/specs/2026-09-14-claimlock-design.md` (v1, amendments 1–11).

## 1. Problem

claimlock v1 stores claims in a committed `claims/` directory, so a team already
shares them. But v1 was designed around one person verifying one claim at a
time, and several of its mechanics fight a team:

| # | Friction in v1 | Where |
|---|---|---|
| F1 | Two developers who re-verify the same claim against **identical** content still get a text merge conflict, because `verify` rewrites `verified_at` with the current time | `ops.verify` |
| F2 | Two branches pinning **different** content conflict on `blob:` lines, and the only guidance is "take either side, re-check, re-verify" | README Limits |
| F3 | Prior content for `diff` exists only in git or the verifier's gitignored `.claimlock/objects/`; other clones often get "pinned content unavailable" | `snapshots.store` |
| F4 | A refactor by A stales claims written by B; a strict `check` in CI blocks A on claims A does not understand, which invites re-verifying without re-checking | `cli.cmd_check` |
| F5 | No record of who vouched for a claim, so a stale claim cannot be routed to someone who understands it | claim format |
| F6 | LF and CRLF checkouts hash differently; documented as a limit, not solved | `pins.blob_of_bytes` |
| F7 | Hooks cannot say whose change staled a claim, nor distinguish your uncommitted drift from drift that arrived by pull | `hooks.stop`, `hooks.head_check` |

Two defects open at the v1 merge also produce a **false clean** in a team gate
and are fixed first (§7).

## 2. Decisions (made in brainstorming)

| Question | Decision |
|---|---|
| Who owes the re-check when a change stales someone else's claim? | **The change author, scoped.** CI blocks only on claims the change touched; pre-existing drift never blocks an unrelated change. The author re-checks or explicitly hands off. |
| Where does a hand-off live? | **In the claim file**: `status: owed`, `owed_by`, `owed_since`, committed with the change. |
| How does every clone see prior content? | **Pins must be anchored in git history.** A pin whose content never appeared in a commit at that path is reported `unanchored`. Snapshots are removed. |
| Line endings | **Hash git's normalized content** inside git (`git hash-object --stdin-paths`); raw bytes outside git. |
| Who verified, and when | **Derived from git history**; `verified_at` is dropped from the file. |
| Two branches pin different content | **Plain text merge + `claimlock resolve`.** No custom merge driver (it would need per-clone git config and behave differently on fresh CI clones). |

## 3. Evidence behind the decisions

Observed 2026-09-14 on git 2.55.0 in throwaway repositories (commands kept in
the brainstorming record; results verbatim):

- **Normalized hashing settles line endings.** A file committed as
  `e5c5c5583f49…`; a second clone with `core.autocrlf=true` holds CRLF bytes.
  Raw hash there: `cf9b2a85b62b…`. `git hash-object --path=src.txt src.txt` and
  `git hash-object --stdin-paths`: `e5c5c5583f49…` — equal to the committed blob.
- **Anchoring must use history, not the object store.** For a blob written by
  `git hash-object -w` and for a blob staged then unstaged, `git cat-file -e`
  reports **present**, yet `git log --all --find-object=<blob>` finds **0**
  commits; for committed content it finds 1. (This also shows v1's
  `snapshots.store` can skip saving a snapshot for content git may later prune.)
- **Who/when is recoverable.** After Alice commits a claim pinning a blob and Bob
  adds an unrelated commit, `git log -1 --format='%an %aI %h' -S"blob: <sha>" --
  claims/c.md` returns Alice and her commit time.
- **Cost is small.** On a repository with 4,270 commits:
  `git log --all --find-object=<blob> -- <path>` 0.019–0.020 s;
  `git rev-list --objects --all | grep` 0.013 s (cache state not controlled —
  re-measure cold before relying on it); `git log -1 -S… -- <dir>` 0.013 s.

## 4. Data model

### 4.1 Claim file

- `verified_at` is **removed**. Files carrying it are accepted (the field is
  ignored, not an `unknown field` problem); `verify` deletes the line when it
  rewrites a claim.
- `status` gains `owed`. Allowed statuses: `verified`, `unverified`, `refuted`,
  `owed`.
- New fields, valid only with `status: owed` (a problem otherwise):
  - `owed_by` — an email; required.
  - `owed_since` — a commit id (7–40 hex) or `none` outside git; required.
- An owed claim keeps its `sources` pins unchanged, so `diff` still shows what
  moved.
- `verify` sets `status: verified` and removes `owed_by` / `owed_since`.

Example:

```markdown
---
id: retries-are-capped
area: http
status: owed
owed_by: bob@example.com
owed_since: a1b2c3d
evidence:
  - kind: test
    ref: tests/test_limit.py::test_clamp_caps_at_max
sources:
  - path: src/limit.py
    blob: e5c5c5583f49a34e86ce622b59363df99e09d4c6
---
`clamp()` caps retries at `MAX`.

Owed 2026-09-14 by alice@example.com: `MAX` moved into config; needs a re-check.
```

### 4.2 Per-claim states

Problems (`invalid`, as v1) plus one new problem class:

- **`conflicted`** — the claim file contains a line starting with `<<<<<<< `,
  `=======` (exact), or `>>>>>>> `. Reported as `conflicted` with the hint
  `claimlock resolve`, not as a generic parse error. A conflicted claim has no
  freshness.

Freshness for `status: verified` (worst wins: `missing` > `stale` >
`unanchored` > `unpinned` > `fresh`):

- `missing`, `stale`, `unpinned`, `fresh` — as v1 (with §4.3 hashing).
- **`unanchored`** — inside git only: a pin's blob never appeared in any commit
  at that source path.

`owed` claims have no freshness verdict; they are listed with owner and age
(`owed_since` → commits and days behind HEAD).

### 4.3 Hashing

- Inside a git work tree: pins are computed by one `git hash-object
  --stdin-paths` over all sources to hash, so git's `text`/`eol`/`autocrlf`
  normalization applies and every clone agrees.
- Outside git, or if that call fails: raw-bytes SHA-1 blob, as v1.
- The stat cache stays in front of hashing, keyed additionally by the hashing
  mode (`git` / `raw`) so a clone that gains or loses git never trusts an entry
  from the other mode.
- Existing pins stay valid wherever git applies no conversion to that file.

### 4.4 Anchoring

- One `git rev-list --objects --all -- <every cited source path>` per run; the
  set of listed blob ids is the anchor set. A pin is anchored iff its blob is in
  that set. (If measurement shows this is slow cold on large histories, fall
  back to per-pin `git log --all --find-object=<blob> -- <path>` only for pins
  not found in the reachable-from-HEAD tree of that path.)
- Outside git: anchoring is not evaluated.

### 4.5 Snapshots

Removed. `diff` reads prior content from git only. For an `unanchored` pin it
says the pinned content was never committed and cannot be shown. v1's
`.claimlock/objects/` is no longer read or written.

## 5. Commands

### 5.1 `claimlock check --changed <base>`

The team gate for CI and pre-merge.

1. `M = git merge-base <base> HEAD`; changed paths = `git diff --name-only
   --no-renames M HEAD` (committed changes only), made root-relative.
2. A claim is **in scope** if any cited source path is changed, or its own claim
   file is changed.
3. **Blocking** (exit 1): in-scope claims that are invalid, conflicted, or whose
   freshness is `stale`, `missing`, `unpinned` or `unanchored`.
4. **Listed, not blocking**: `owed` claims (owner, age); out-of-scope problems
   under the heading `pre-existing (not changed here)`.
5. Census line: `claimlock: N claims (K in scope), M sources hashed — …`.
6. Exit 2 if `<base>` cannot be resolved or the store is outside git (the flag
   needs history; the message says so).

Plain `claimlock check` (no flag) is unchanged: strict over the whole store.

### 5.2 `claimlock owe <id>... [--to <email>] [--reason <text>]`

- Sets `status: owed`, `owed_by` (default `git config user.email`; exit 2 with a
  message if neither `--to` nor a git email exists), `owed_since` (HEAD short id,
  or `none` outside git).
- Refuses a claim that is `fresh` (nothing is owed), `refuted`, `unverified` or
  already `owed` to the same person.
- `--reason` appends one line to the claim body:
  `Owed <YYYY-MM-DD> by <current git email or "unknown">: <text>`.

### 5.3 `claimlock resolve [<id>...]`

Run after a merge that left conflict markers in claim files; with no ids, every
`conflicted` claim.

- Parses each conflict hunk. A hunk wholly inside the `sources` block is
  resolved:
  - hash every cited source's current working-tree content (§4.3);
  - for each source, keep the side whose pin equals the current hash;
  - if every source got a matching pin → the claim keeps its status with those
    pins;
  - otherwise → keep matching pins where they exist, keep "ours" for the rest,
    and set `status: owed`, `owed_by` = current git email, `owed_since` = HEAD.
- A hunk outside the `sources` block (body, evidence, other fields) is left in
  place; `resolve` names the file and exits 1.
- A hunk that spans the `sources` boundary is left entirely in place (exit 1).
- Never stages, commits, or touches non-claim files. Exit 0 when every requested
  claim is conflict-free afterwards.

### 5.4 `claimlock show <id>` and `claimlock who <id>`

- `show` adds, per source: `verified by <author email> at <ISO time> (<short
  commit>)` from `git log -1 --format='%ae %aI %h' -S"blob: <sha>" -- <claim
  file>`; `uncommitted` if the pin is not in any commit of the claim file;
  `unknown (no git)` outside git.
- `who <id>` prints only those lines, tab-separated, for scripts.

### 5.5 Filters

`claimlock stale` and `claimlock list` gain `--owed-by <email>` and `--mine`
(uses `git config user.email`). `stale` lists `owed` claims too (state column
`owed`).

## 6. Hooks with a team

All v1 guarantees stand: never exit non-zero, never set `decision`, inert
without `.claimlock.toml`, output ≤ 2,000 characters, per-session lock. Hooks
never run `owe` or `resolve`.

- **SessionStart** (reaches Claude): adds `owed to you: N (<ids>)` first when
  `N > 0` (git email cached in session state), and counts `unanchored` and
  `conflicted` beside the existing counts.
- **PostToolUse when HEAD moved** (reaches Claude): for each claim that is no
  longer fresh and cites a changed path, attribute the change:
  `retries-are-capped (stale): src/limit.py changed by bob@example.com in
  a1b2c3d "bump MAX"`. One `git log --no-renames --name-only --format=%x00%h%x09%ae%x09%s
  <old>..<new>` supplies all attributions. Also report claims that became owed to
  you in the range, and, if any claim file is conflicted, `run claimlock resolve`.
- **Stop** (reaches the user): the "since the last check" report splits into
  - drift whose source paths appear in `git diff --name-only HEAD` → `from your
    uncommitted edits: …`;
  - drift already attributed by a HEAD-moved report in this check → referenced,
    not repeated (v1 dedupe).
- **Cost:** attribution, owed-to-you and conflict detection run only when HEAD
  moved; the common path remains `stat` calls.

## 7. Prerequisites (fixed first)

1. **Store inside an ignored directory.** When `git check-ignore -q .` succeeds
   from the project root, marker candidates come from the directory walk, not
   `git ls-files`. Test: an outer repo ignoring `scratch/`, a store at
   `scratch/proj` with a dangling marker → `refs` exit 1.
2. **Unreadable claims directory.** `load_claims` raises the store-unreadable
   error (exit 2) when `claims_dir` exists but cannot be listed. Test with
   `chmod 000` (skipped as root).

## 8. Migration

- `verified_at` in existing claims: accepted and ignored; removed on next
  `verify`.
- `.claimlock/objects/`: no longer used; README tells users it may be deleted.
- Pins: valid unless git applies a line-ending conversion to that file; such
  claims read stale once (fail-safe) and one `verify` settles them for all
  clones.
- README gains a "Using claimlock as a team" section and a v1→teams migration
  note; the v1 Limits entries for line endings and pin conflicts are rewritten.

## 9. Testing

Stdlib `unittest`, real git, multi-clone fixtures (bare origin + clones), every
new detector shown failing by a mutation.

- Scoped gate: a branch editing a cited source blocks; unrelated pre-existing
  drift is listed and does not block; an `owed` claim does not block; a claim
  file edited in range with an `unanchored` pin blocks; bad `<base>` exits 2.
- Identical re-verification on two branches merges with no conflict.
- `resolve`: merged content matching one side keeps that pin; matching neither
  → `owed` by the merger; a conflict in prose is left and exits 1; a hunk
  spanning the sources boundary is left.
- `owe`: sets fields, refuses fresh/refuted/unverified, `--reason` line, round
  trip through a merge, `verify` clears it.
- Line endings: LF clone and `autocrlf=true` clone agree (both fresh); outside
  git raw hashing still detects a CRLF change.
- Anchoring: committed pin anchored; `hash-object -w` blob and
  staged-then-unstaged blob `unanchored`.
- Who/when: `show`/`who` report the verifier after later unrelated commits;
  `uncommitted` before the claim is committed; `unknown (no git)`.
- Hooks: attribution line after a pull; owed-to-you after a pull; conflict
  notice after a conflicted merge; Stop separates uncommitted drift from pulled
  drift; PostToolUse with HEAD unchanged spawns no git (asserted with a PATH that
  has no git). Stop and SessionStart may call git (hashing, anchoring) and are
  not covered by that assertion.
- Prerequisites §7 as specified.

Skills: `using-claimlock` (owe vs re-check; `owe` is a hand-off, not a way past
the gate), `operating-claimlock` (`check --changed` CI recipe, `resolve`, line
endings now automatic in git). One new closed-book pressure scenario: "a
teammate's refactor stales your claims right before a release".

## 10. Non-goals

- A custom git merge driver (A can back one later: it would call `resolve`).
- Symbol- or region-level pins.
- Forge integration (issues, PR comments, CODEOWNERS parsing).
- Deciding whether a claim is semantically still true.

## 11. Risks

- **`resolve` choosing pins for content nobody re-checked.** It only keeps a pin
  that equals the merged content, so the result is exactly what one side verified;
  anything else becomes `owed`. Never a pin nobody verified.
- **`--changed` misses drift introduced by a merge commit itself** (conflict
  resolution edits). Mitigation: merge commits' own changes are part of `M..HEAD`
  diff; the test suite covers a conflicted merge followed by `check --changed`.
- **Anchor lookup cost on very large histories.** Measured small at 4,270 commits;
  re-measured cold during implementation, with the per-pin fallback in §4.4.
- **Normalized hashing needs a git subprocess per hashing pass.** Bounded by the
  stat cache; hooks keep their common path free of git.
