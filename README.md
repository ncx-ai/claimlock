# claimlock

A Claude Code plugin that pins written claims about a codebase to the content
of the files that could falsify them — a small CLI, three non-blocking hooks,
and skills that teach an agent when to check and when to write. It prevents
the specific failure of docs that keep asserting things that stopped being
true, with nothing to catch it: a claim in `claims/` is tied to a git blob
hash of its source files, so the moment a cited file's content changes, the
claim goes stale and `claimlock check` fails. Nothing here can make a claim
true — it only makes a claim that has drifted impossible to miss.

**Claims are shared, not local.** `claims/` is ordinary project content: you
commit it, and everyone working in the repository verifies, edits and reads
the same claim files. The only thing kept out of git is `.claimlock/` — a
per-clone stat cache and pinned-content snapshots used by `diff` when git
itself doesn't have the old blob. Two clones with the same `claims/` history
converge to the same freshness state; `.claimlock/` never needs to.

## Install

```
/plugin marketplace add <owner>/claimlock
/plugin install claimlock@claimlock
```

Requires Python ≥ 3.11 (standard library only — nothing to `pip install`).
On an older `python3` the CLI exits 2 with a message, and the hooks stay
silent and log one line to `hook-errors.log` rather than fail.
Git is optional: it improves `claimlock diff` (reading prior content straight
from git's object store) and powers commit/HEAD-movement detection in the
hooks, but the CLI works fully in a plain, non-git directory.

## Thirty-second tour

```
claimlock init                        # .claimlock.toml, claims/, .gitignore entry
claimlock new retries-are-capped --area core
# edit claims/retries-are-capped.md: write the claim, cite evidence and sources
claimlock verify retries-are-capped   # pins every cited source, marks it verified

# time passes; a cited source changes...
claimlock check                       # STALE retries-are-capped — exit 1

claimlock diff retries-are-capped     # see exactly what changed since verification
```

## Commands

| Command | Does |
|---|---|
| `claimlock init` | Create `.claimlock.toml`, `claims/`, and add `.claimlock/` to `.gitignore`. |
| `claimlock new` | Scaffold an unverified claim (`claimlock new <id> --area <area>`). |
| `claimlock check` | The gate: exit 1 if any claim is invalid, stale, missing, or unpinned. `--changed <base>` scopes blocking to claims whose sources or files changed since the merge base with `<base>` — everything else wrong is listed as pre-existing, and `owed` claims are listed but never block. |
| `claimlock stale` | List non-fresh verified claims, tab-separated, with their changed paths. |
| `claimlock list` | List every claim with its status and freshness flag. |
| `claimlock search` | Case-insensitive substring search over id, area, body, sources and evidence refs. |
| `claimlock show` | One claim in full: status, freshness per source, evidence, body. |
| `claimlock verify` | Pin every source to its current content and mark the claim verified. |
| `claimlock owe` | Hand off a claim's re-check to someone (`--to <email>`, default your git `user.email`); status becomes `owed`. |
| `claimlock resolve` | Settle conflicted `sources` pins after a merge: keep a pin only when it matches the merged content, else mark the claim owed by the merger. |
| `claimlock diff` | Show what changed in a claim's sources since it was last verified. |
| `claimlock who` | Who verified each of a claim's pins, from git history (email, timestamp, commit). |
| `claimlock refs` | Fail if any `` Claim: `id` `` marker in prose names no claim. |
| `claimlock affected` | List claims whose sources include the given path(s). |
| `claimlock import` | Import claims from the original (unpinned) ground-truth format. |
| `claimlock self-test` | Prove the freshness/dangling-marker detectors actually fire, on this machine. |
| `claimlock hook` | The Claude Code hook entry point (`claimlock hook <event>`); always exits 0. |

Every command accepts `-C <dir>` to run as though started in `<dir>` — but
`-C` is an option of `claimlock` itself, not of the subcommand, so it must
come **before** the subcommand name: `claimlock -C <dir> check` works,
`claimlock check -C <dir>` errors (`unrecognized arguments: -C <dir>`). Full
field-level and format detail: [`docs/format.md`](docs/format.md).

## Hooks

| Hook | Who sees it | When |
|---|---|---|
| SessionStart | Claude (as context) | Counts of non-fresh/invalid claims and the areas they're in, plus a reminder to search before asserting. |
| PostToolUse (after Bash / MCP tool calls) | Claude (as context) | HEAD moved since the last check, and the commits in that range changed sources of now-non-fresh claims or introduced dangling markers. |
| Stop | The user (a `systemMessage`) | Problems *since the last check* in this clone (not pre-existing ones) — new stale/invalid claims or dangling markers, whether this session's edits or a `git pull` caused them — plus any HEAD movement. A claim named in the HEAD-moved report is not listed twice. |

Hooks **never block**: they always exit 0 — including under a `python3` older
than 3.11, where they print nothing and log one line to `hook-errors.log` in
the plugin data directory — never set `decision`, and a Stop
warning does not continue the turn — it is shown to the user only, after
Claude has already finished responding. Hooks are active only in a project
that has a `.claimlock.toml` — in the project directory Claude Code opened, or
one of its ancestors (a config in a *subdirectory* of the opened project is not
seen). Anywhere else, including a repository that merely has a `claims/`
directory, every hook prints nothing at all and runs no git command.

## Skills

| Skill | Purpose |
|---|---|
| `using-claimlock` | How to read before asserting and write after establishing — the day-to-day discipline of searching claims before stating a fact and registering one after proving it. |
| `operating-claimlock` | Adopting claimlock in a repo, wiring the gate into CI, importing an older claim store, and triaging a pile of stale claims. |
| `design-lenses` | Eight independent lenses (correctness, scale, concurrency, falsifiability, cost, consumer experience, operability, claim integrity) for judging when work is actually done. |
| `evidence-standards` | What makes a green result meaningful — falsifiable checks, non-zero pass counts, sentineled instruments — versus a mechanism that looks like enforcement and enforces nothing. |

## How staleness works

Each pinned source records a **git blob hash** — `sha1("blob <len>\0" +
bytes)`, the same value `git hash-object` would produce — computed without
requiring git at all. That's deliberate:

- **Not timestamps.** An mtime survives a checkout, a copy, or an editor
  touching the file without changing it, and can go backward across a branch
  switch. Content hashing can't be fooled either way: same bytes, same hash,
  regardless of when they were written.
- **Git is optional, not required.** A store can be created and verified in a
  plain directory; running `git init` afterward doesn't invalidate anything,
  because the hash formula never depended on git being present.
- **`diff` needs the old content**, not just a hash mismatch. When git holds
  the pinned blob, `diff` reads it straight from git's object store
  (`git cat-file blob <sha>`). When it doesn't — no repository, or the content
  was verified but never committed — claimlock keeps its own copy under
  `.claimlock/objects/<blob>`, pruned to exactly the blobs still referenced by
  a claim each time `verify` runs.
- **A stat cache avoids re-hashing unchanged files** on every run
  (`.claimlock/cache/stat.json`, keyed by `(size, mtime_ns)`). It has a 2-second
  *racy-timestamp guard*, the same one git uses: an entry whose mtime is under
  2 seconds old is never cached, so a same-size edit within one filesystem
  clock tick can't be missed by trusting a stale cache entry.
  **The trade-off:** an entry at least 2 seconds old is trusted whenever the
  file has the same size and the same `mtime_ns`, without re-reading it. A tool
  that rewrites content but restores the timestamp — `cp -p`, `rsync -a`, a
  build cache that restores mtimes — can therefore hide a same-size edit from a
  warm cache. A fresh clone or a CI run has no cache and always hashes;
  deleting `.claimlock/cache/` forces the same locally.

## CI

```
claimlock self-test && claimlock check && claimlock refs
```

`self-test` proves the detectors can actually fire on this machine before
trusting `check`/`refs` to mean anything; `check` is the freshness/validity
gate; `refs` fails on any prose marker naming no claim.

## Limits

- **File-level granularity.** A pin covers a whole file's bytes; a whitespace
  reformat, a line-ending conversion, or an unrelated edit elsewhere in a
  large shared file all make every claim citing it stale, whether or not the
  cited behavior changed. Prefer the narrowest file that actually enforces
  the behavior when writing `sources`.
- **Line endings across clones.** A pin hashes a file's exact working-tree
  bytes. If one clone checks a file out with LF and another with CRLF
  (`core.autocrlf=true`, or a Windows runner), every claim citing it is stale
  in the other clone — and re-verifying there makes it stale for everyone
  else. This fails safe (never a false fresh), but it never settles. In a
  repository used across platforms, commit `* text=auto eol=lf` to
  `.gitattributes` (or set `core.autocrlf=false`) so every clone holds the
  same bytes.
- **Pin conflicts on merge.** Two branches that both verified the same claim
  conflict on its `blob:` lines in a text merge. Resolve by taking either
  side, then re-check and re-`verify` — never trust a merged pin you didn't
  re-derive.
- **Remote-only commits aren't seen until pulled.** HEAD-movement detection
  watches local ref files; a commit that only exists on a remote has no
  effect on this clone's hooks until something moves local HEAD (pull, fetch
  + merge/rebase, checkout).
- **`reftable` repositories.** HEAD-movement detection uses a cheap stat gate
  over `HEAD`, `logs/HEAD`, `packed-refs` and the current branch's files —
  files that don't exist in a repository using git's newer `reftable` ref
  storage. On such a repository the stat gate never trips, so hooks silently
  stop reporting commits (`claimlock check` run directly is unaffected — it
  always re-evaluates from scratch).
- **Coarse filesystem mtimes.** HEAD-movement detection (`hooks.py`'s
  `_stat_marks`/`head_check`) gates on a raw `os.stat().st_mtime_ns` snapshot
  of git's `HEAD`, current-ref and reflog files, with **no** racy-timestamp
  guard — that guard belongs to a different mechanism (the content stat cache
  above). On a filesystem with second-or-coarser mtime resolution, a commit
  landing within the same tick as the last probe can leave that snapshot
  looking unchanged, so the hook doesn't notice it that turn. Nothing is
  lost, only delayed: the stale comparison keeps failing to match until a
  later tick's stat can tell the two apart, at which point the report covers
  the whole range since the last one actually seen — the same reason a
  transient git failure mid-check also only delays rather than drops commits
  (the marks are restated and moved forward, but the last known HEAD is held
  onto until git succeeds again). `claimlock check` run directly is
  unaffected, since it always re-evaluates from scratch. (The content stat
  *cache's* racy guard is unrelated to HEAD detection: it exists so a
  same-tick **content** edit is never missed by `check` — an entry younger
  than 2 seconds is never cached, so it's re-hashed instead of trusted.)
- **Marker scanning outside git walks the whole tree.** Inside a git work
  tree, `claimlock refs` and the SessionStart/Stop hooks take candidate files
  from one `git ls-files --cached --others --exclude-standard`, so gitignored
  trees (`target/`, `build/`, `vendor/`…) cost nothing — and markers in them,
  or inside git submodules, are not scanned. Outside git, or if that git call
  fails, every non-hidden directory except `node_modules/` and the claims
  directory is walked, so a large untracked tree makes each Stop slower.
- **Lock files accumulate.** The per-session hook lock
  (`<plugin data dir>/sessions/<session-id>.lock`) is left in place after use
  rather than removed — harmless (an empty file, reused by session id) but it
  means the `sessions/` directory grows by one file per distinct session ever
  seen, never shrinking on its own.
- **Everything here is file-level and content-level, never semantic.**
  claimlock cannot tell whether a change to a cited file actually invalidates
  the claim's text — it only tells you the bytes changed and the claim is
  owed a look. That look is the point: see `claimlock diff` and the
  `using-claimlock` skill.
