# claimlock — design

Date: 2026-09-14. Status: approved in brainstorming, awaiting spec review.

## 1. What it is

A **Claude Code plugin** that pins written claims about a codebase to the exact
content of the files that could falsify them, and makes a claim **loud** the
moment that content changes. It ships:

- `claimlock` — a stdlib-only Python CLI: a `bin/claimlock` launcher (on the
  Bash tool's PATH via the plugin's `bin/`) backed by the `lib/claimlock/`
  package.
- Four skills: `using-claimlock`, `operating-claimlock`, `evidence-standards`,
  `design-lenses`.
- Three hooks: SessionStart (baseline + context), Stop (user-facing warning),
  PostToolUse (HEAD-movement warning).

It cannot make a claim true. It makes a claim that has drifted impossible to
miss.

### Origin, and what this fixes

Extracted from a private project's claim store. The extraction fixes defects
found while reading it (2026-09-14):

| Defect in the original | Resolution here |
|---|---|
| The original's `show` command raises `NameError` on every claim (`is_stale` is a string, `state` undefined) | Rewritten; `show` is covered by a test |
| README says both "timestamp-granular" and "by DATE, not by timestamp" | Staleness is content-based; no time comparison exists to describe |
| Staleness reads only committed history — an uncommitted edit to a source is invisible | Pins hash working-tree content |
| Same-day ambiguity (`unproven` state) and rebase/cherry-pick rewriting commit dates | No dates in the staleness decision |
| A `Claim: \`id\`` marker naming a nonexistent claim is invisible to every gate | `claimlock refs` |
| Requires PyYAML | Strict stdlib frontmatter parser |
| Requires git | Git-free core; git is an enhancement |

### Non-goals (v1)

- Region/symbol-scoped pins (hash part of a file). File-level pins stay noisy
  on very large files; revisit with measurements in v2.
- Automatically deciding whether a claim is still true. Re-checking is human/agent
  work; the tool only says *that* it is owed.
- Observing commits that never touch the local checkout (e.g. `gh api` creating a
  commit on a remote). Detected only once they are pulled and HEAD moves.
- Migrating the origin project. A separate task.

## 2. Repository layout

```
claimlock/
  .claude-plugin/plugin.json        # name "claimlock", description "Claude Code plugin: ..."
  .claude-plugin/marketplace.json   # single-plugin marketplace, source "./"
  bin/claimlock                     # launcher; python3 >= 3.11; puts lib/ on sys.path
  lib/claimlock/                    # the package: frontmatter, project, pins, claims,
                                     # gitio, snapshots, ops, refs, importer, selftest,
                                     # hooks, cli
  hooks/hooks.json                  # every hook runs `claimlock hook <event>`
  skills/
    using-claimlock/SKILL.md
    operating-claimlock/SKILL.md
    evidence-standards/SKILL.md
    design-lenses/SKILL.md
  tests/helpers.py  tests/test_*.py # unittest, stdlib only
  docs/format.md                    # the claim file format, normative
  docs/specs/                       # this document
  README.md  LICENSE
```

Install: `/plugin marketplace add <owner>/claimlock` then
`/plugin install claimlock@claimlock`.

## 3. The store

### Root discovery

The project root is, in order: the nearest ancestor directory containing
`.claimlock.toml`; else the git toplevel; else the current directory. Because
`init` writes `.claimlock.toml`, a store's root is anchored by its config, not by
git — running `git init` later does not move it.

### Config — `.claimlock.toml` (optional)

```toml
claims_dir = "claims"                  # default
marker_globs = ["**/*.md"]             # prose scanned by `refs`; default
marker_pattern = 'Claim: `([a-z0-9][a-z0-9-]*)`'   # default; group 1 = id
```

Unknown keys are an error (a typo must not silently fall back to a default).

### Claim file — `<claims_dir>/<id>.md`

One claim per file. The filename stem must equal `id`.
`<claims_dir>/README.md` is reserved for human notes and is never loaded as a
claim.

```markdown
---
id: ledger-conserves-money
area: route-pricing
status: verified
verified_at: 2026-09-14T14:38:58-04:00
evidence:
  - kind: test
    ref: "ledger_live_tests::settle_and_release_racing_have_exactly_one_winner"
sources:
  - path: crates/pricing/src/ledger/ops.rs
    blob: 3f2a9c0d1e...
---
One sentence stating the claim, present tense.

Why it is true, the enforcement site, what would falsify it.
```

| Field | Rule |
|---|---|
| `id` | kebab-case, equals filename stem |
| `area` | free string; default `unfiled` |
| `status` | `verified` \| `unverified` \| `refuted` |
| `verified_at` | ISO-8601 timestamp written by `verify`. **Informational only** — never used to decide staleness |
| `evidence` | list of `{kind, ref}`; `kind` ∈ `test`, `measurement`, `source`, `run` |
| `sources` | list of `{path, blob}`; `path` is root-relative, POSIX separators, must not escape the root. `blob` is written only by `verify`. A bare string entry is accepted as an unpinned source |

**Frontmatter parser.** A strict YAML subset: `key: scalar`, `key:` followed by a
list of scalars or a list of flat `key: scalar` maps; scalars plain, single- or
double-quoted; `[]` for an empty list; `#` comments. Anything else is a parse
error naming file and line — never a best-effort guess. `verify` rewrites only
`verified_at` and the `sources` block, in canonical form, preserving the body and
every other line byte-for-byte.

### Content pins

`blob = sha1(b"blob " + str(len(data)).encode() + b"\0" + data)` over the file's
raw bytes — identical to `git hash-object --no-filters`. Computed in Python, so:

- the gate never needs git;
- pins written before `git init` remain valid after it;
- the git object store can serve prior content for `diff` when it has the blob.

(Under `core.autocrlf` or clean filters the index blob may differ from this hash,
so `diff`'s git lookup misses and falls back to the snapshot cache. Within one
clone staleness is unaffected — it compares this hash with itself. **Across
clones it is not:** a pin verified on an LF checkout never matches the CRLF bytes
of an `autocrlf=true` clone or a Windows runner, so the claim is permanently
stale there, and verifying there makes it stale for every LF clone. This fails
safe — never a false fresh. Normalisation is deferred to the multi-editor design;
for v1, repositories used across platforms should set `* text=auto eol=lf` in
`.gitattributes` or `core.autocrlf=false`.)

**Stat cache.** `(path, size, mtime_ns) → blob`, stored at
`.claimlock/cache/stat.json` — so the Stop hook re-reads only files whose
metadata changed. A cache hit is never trusted when size or mtime differs. An
entry is never stored while the file's mtime is < 2 s old (a racy-timestamp
guard, as git does), so a same-size edit within the clock tick cannot be
missed.

**Snapshot cache.** On `verify`, each pinned file's content is written to
`.claimlock/objects/<blob>` unless git already holds that blob. After writing,
objects referenced by no pin are deleted. `init` adds `.claimlock/` to
`.gitignore` (creating it if needed).

### Per-claim states

A claim has zero or more **problems** and exactly one **freshness**:

- problems (`invalid`): bad status/kind, missing body, id ≠ filename, a
  `verified` claim with no evidence, a `verified` claim with no sources (it could
  never go stale — prose with extra steps), a source path that escapes the root,
  parse error (unparseable frontmatter makes the claim `invalid`; the store
  itself is still readable, so `check` exits 1 for it, never 2).
- freshness, evaluated only for `status: verified`:
  - `missing` — a source path does not exist or cannot be read (an unreadable
    file is reported, never raised)
  - `unpinned` — a source has no `blob`
  - `stale` — a source's current blob ≠ its pin
  - `fresh` — none of the above

Precedence when several sources disagree: `missing` > `stale` > `unpinned` >
`fresh`; `show` lists every source's individual state.

`unverified` and `refuted` claims are never stale (nothing is asserted to hold).

## 4. CLI

Exit codes everywhere: **0** clean, **1** findings (including any claim whose
frontmatter cannot be parsed — reported `invalid`, never hidden), **2** the
store could not be read (bad config, no store where one was required).

| Command | Behaviour |
|---|---|
| `init` | Write `.claimlock.toml` + `claims/` + `.gitignore` entry; print the CI snippet. Refuses if a store exists |
| `new <id> [--area A]` | Scaffold an `unverified` claim |
| `check [--json]` | The gate: every claim's problems + freshness. Always prints the census line `N claims, M sources hashed` — so "found nothing" and "saw nothing" never print the same. Exit 2 if `claims_dir` does not exist (a misconfigured path must not read as an empty, clean store) |
| `stale` | Non-fresh verified claims, one per line (tab-separated) |
| `list [--area] [--status]` | Headline per claim with freshness flag |
| `search <query>` | Case-insensitive substring over id, area, body, sources, evidence refs |
| `show <id>` | Full claim, evidence, each source with its own state |
| `verify <id>...` | Refuses a claim with problems, no evidence, or `status: refuted` (un-refuting is a manual edit). Sets `status: verified`, pins every source to its current blob, writes `verified_at` (local offset, seconds), refreshes the snapshot cache. Prints what was pinned |
| `diff <id>` | For each changed source: unified diff from pinned content (git object, else snapshot) to current. If neither has it, says so explicitly |
| `affected <path>...` | Claims whose sources include any given path |
| `refs` | Scan `marker_globs`, always excluding `claims_dir`, hidden directories (`.git/`, `.claimlock/`, …), `node_modules/` and — inside a git work tree — gitignored files (amendment 10); exit 1 on any marker naming no claim; census line `K markers in F files` |
| `import <dir>` | Convert the origin format (`sources` as plain strings, date/timestamp `verified_at`): writes claims with unpinned sources, so every imported `verified` claim reports `unpinned` — and `check` exits 1 — until each is re-checked and verified. Deliberate: an import must not launder old verifications into fresh pins |
| `self-test` | In a temp dir: pin a source then edit it → must report `stale`; delete it → `missing`; dangling marker → `refs` exits 1; repeat after `git init`. Exit 1 if any detector fails to fire |
| `hook <event>` | Hook entry points (§5). Always exit 0 |

## 5. Hooks

Every hook exits 0 with no output when neither the project directory
(`$CLAUDE_PROJECT_DIR`) nor any of its ancestors contains `.claimlock.toml` — an
installed plugin is inert in repositories that do not use it. A bare `claims/`
directory is not a store for hooks (amendment 8), and the decision is made with
plain `stat` calls, before any git subprocess or data-directory write. No hook ever exits 2 or sets `decision` — including under a `python3` older than
3.11, where the launcher logs one line and exits 0 before its version check
can fail the hook. A hook
that fails internally prints nothing to Claude and writes the error to
`${CLAUDE_PLUGIN_DATA}/hook-errors.log` (a crashing warning must not become a
blocking error).

Session state lives at `${CLAUDE_PLUGIN_DATA}/sessions/<session_id>.json`:
the baseline problem sets (stale / missing / unpinned / invalid / dangling
markers) and `last_head` (commit sha, or null outside git). A dangling
marker's identity in the baseline is `path:id` (not its line number), so an
edit that moves the marker within the file does not re-warn.

### Documented behaviour this relies on

Verified against `https://code.claude.com/docs/en/hooks.md` and
`plugins-reference.md` on 2026-09-14. Re-verify these in the implementation's
first task by running the hooks in a real session — docs are evidence of
intent, not of behaviour.

| Fact | Source |
|---|---|
| SessionStart `hookSpecificOutput.additionalContext` is added to Claude's context; matchers `startup`, `resume`, `clear`, `compact`, `fork` | hooks.md, SessionStart decision control; matcher table |
| Stop `hookSpecificOutput.additionalContext` **continues the conversation** (a soft block) — therefore not used | hooks.md, Stop decision control |
| `systemMessage` is a "warning message shown to the user" | hooks.md, JSON output table |
| PostToolUse `additionalContext` is added to Claude's context alongside the tool result | hooks.md, PostToolUse decision control |
| Hook output strings capped at 10,000 characters | hooks.md, JSON output |
| `bin/` is added to the Bash tool's PATH; `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR` exported to hooks | plugins-reference.md |

### SessionStart (`startup|resume|clear|compact|fork`)

Compute the baseline, record it and `last_head`, and emit
`additionalContext` (≤ 2,000 chars):

> claimlock: 32 claims — 22 stale, 0 missing, 3 unpinned, 0 dangling markers.
> Stale areas: llm-gateway (9), grpc (7), … Search the store (`claimlock search
> <topic>`) before asserting a limit or guarantee; re-check before re-stamping.

When everything is fresh, one line.

### Stop

Recompute. Emit `systemMessage` (user-facing; the turn ends normally) **only for
problems not in the baseline** — so pre-existing drift does not repeat every
turn, but drift that appeared since the last check does. That includes drift
that arrived by `git pull` or any other change to the checkout, so the message
says "since the last check", never "this session" (amendment 11). A claim or
marker already named in the same output's HEAD-moved report is omitted from
this line, and the line is dropped if nothing remains:

> claimlock: since the last check, 2 claims became stale (ledger-conserves-money,
> …). Inspect with `claimlock diff <id>` or `claimlock refs`.

After warning, the baseline is replaced by the current survey (not unioned) so
the same warning is not repeated immediately — but a problem that is fixed and
then re-introduced warns again. Also runs the HEAD check below, which catches
commits made outside any tool call (another terminal, an IDE).

### PostToolUse — HEAD movement (matcher: `Bash|mcp__.*`)

**Commits are detected by HEAD moving, not by matching a command.** A
`git commit` pattern misses `gh`, git aliases, `make release`, scripts, rebases,
merges, pulls and MCP tools. The matcher covers every tool that can run a
process or reach git (Bash, any MCP tool); file tools (Read/Edit/Write) cannot
move HEAD and are excluded, and anything else is caught by the Stop hook. The
cost is one Python interpreter start per matched call (~tens of ms, measure in
the first task). On each matched PostToolUse:

1. Outside git → return immediately.
2. Cheap gate: `stat` the HEAD reflog (`git rev-parse --git-path logs/HEAD`,
   path cached in session state) and the ref HEAD points to; if neither mtime
   changed since last check, return. No subprocess on the common path.
3. Otherwise read HEAD. If equal to `last_head`, update the stat marks and
   return.
4. HEAD moved: changed paths = `git diff --name-only <last_head> HEAD` (if
   `last_head` is unreachable or null, `git diff-tree --no-commit-id --name-only
   -r HEAD`). Claims = `affected(changed)` that are now non-fresh, plus dangling
   markers in changed files matching `marker_globs`.
5. If any, emit `additionalContext` (Claude reads it on its next request; the
   operation already happened, so nothing is blocked):

   > claimlock: HEAD moved a1b2c3d→e4f5a6b (commit/merge/rebase/pull). Committed
   > changes left 2 claims stale: …; 1 dangling marker in docs/x.md.

6. Set `last_head`.

The Stop hook runs steps 1–6 too but reports via `systemMessage`.

## 6. Skills

All four follow superpowers skill format (frontmatter `name` + `description`
beginning "Use when…"), self-sufficient for a session with no knowledge of the
origin project.

- **using-claimlock** — the discipline: search before asserting; write a claim
  only after running something that could have come out otherwise; one fact per
  claim; cite the enforcement site, not the constant; on stale, re-check and
  re-run evidence before `verify`; refute rather than delete; context is not
  evidence. Cross-links `evidence-standards`.
- **operating-claimlock** — adoption (`init`), CI/pre-commit snippet
  (`self-test` → `check` → `refs`), stale triage with `diff`, `import`,
  interpreting each hook's output, large-file noise and when to split sources.
- **evidence-standards** and **design-lenses** — genericised from the origin:
  keep structure and war stories (as language-neutral examples), remove
  project-specific file and platform references.

## 7. Testing

- `python3 -m unittest` over `tests/`, stdlib only, every test in a fresh temp
  directory. The core matrix runs **twice: without git and with git**, plus a
  transition test (pin → `git init` → commit → still fresh → edit → stale).
- Each detector is shown to fire: edit → `stale`, delete → `missing`, unpinned
  verified → `unpinned`, malformed frontmatter → `invalid`, exit 1, naming the
  line, dangling marker → `refs` exit 1, id ≠ filename → invalid.
- `verify` round-trip: body and unrelated lines byte-identical after rewrite.
- `diff` from git object, from snapshot cache, and with neither.
- Hooks as subprocesses fed recorded stdin JSON: inert without `.claimlock.toml`
  (including beside a bare `claims/` directory); exit 0
  always (including an injected internal error); SessionStart emits
  `additionalContext`; Stop emits `systemMessage` only for new problems and not
  twice; HEAD movement via `git commit`, via a commit made by a non-git-named
  script, via `git reset --hard HEAD~1`, and no output when HEAD did not move.
- Skills: RED/GREEN pressure scenarios with subagents per `writing-skills`,
  closed-book (scenarios not readable by the agent under test).
- Manual acceptance: install the plugin from the local path in a real Claude
  Code session and observe each hook's output where the docs say it appears.

## 8. Risks

- **File-level noise.** One edit to a large shared file stales every claim citing
  it. Mitigation in v1: skill guidance to cite the narrowest files; measure before
  building region pins.
- **Pin merge conflicts.** Two branches verifying the same claim conflict on its
  `blob` lines. Resolution: take either side, then `claimlock verify <id>` after
  re-checking.
- **Hook cost at scale.** Stop hashes every source each turn; bounded by the stat
  cache. PostToolUse is a stat on the common path.
- **Documented hook semantics differ from behaviour.** Covered by the manual
  acceptance run before release.

## Amendments (2026-09-14, planning)

1. Layout is `bin/claimlock` launcher + `lib/claimlock/` package.
2. Unparseable claim frontmatter is `invalid` (exit 1), not exit 2.
3. Stop replaces its baseline with the current survey.
4. `verify` sets `status: verified`; refuses `refuted`.
5. Stat cache skips entries with mtime < 2 s old.
6. Dangling-marker identity is `path:id`.
7. `claims_dir/README.md` is not loaded as a claim.

## Amendments (2026-09-14, final review)

8. Hooks are active only when `.claimlock.toml` exists in the project directory
   or one of its ancestors; a bare claims directory is not a store for hooks.
   The check is plain `stat` calls made before `project.load`, so an inactive
   project spawns no git and touches no data directory. The CLI's root
   discovery is unchanged.
9. A file that cannot be read is reported, never raised: an unreadable source
   is `missing`, an unreadable claim file is `invalid`, `verify` refuses, and
   `diff` says so.
10. Inside a git work tree, marker candidates come from `git ls-files --cached
    --others --exclude-standard` (gitignored files are not scanned); outside
    git the tree is walked as before.
11. Stop reports problems not present at the previous Stop check in this clone
    ("since the last check"), not problems "this session introduced": drift
    that arrives by `git pull` is reported too, and a claim already named in
    the same output's HEAD-moved report is not repeated.
