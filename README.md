# claimlock

A Claude Code plugin that pins written claims about a codebase to the content
of the files that could falsify them — a small CLI, four non-blocking hooks,
and skills that teach an agent when to check and when to write. It prevents
the specific failure of docs that keep asserting things that stopped being
true, with nothing to catch it: a claim in `claims/` is tied to the git blob
id of its source files, so the moment a cited file's content changes, the
claim goes stale and `claimlock check` fails. Nothing here can make a claim
true — it only makes a claim that has drifted impossible to miss.

**Claims are shared, not local.** `claims/` is ordinary project content: you
commit it, and everyone working in the repository verifies, edits and reads
the same claim files. The only thing kept out of git is `.claimlock/` — a
per-clone cache (the content stat cache, plus hook session state when the
plugin data directory is not set). Deleting it costs only speed. Two clones
with the same `claims/` history converge to the same freshness state;
`.claimlock/` never needs to.

## Contents

- [Install](#install)
- [Thirty-second tour](#thirty-second-tour)
- [Statuses and states](#statuses-and-states)
- [Commands](#commands)
- [Output size](#output-size)
- [Regions](#regions)
- [Using claimlock as a team](#using-claimlock-as-a-team)
  - [Claims are committed; `.claimlock/` is a per-clone cache](#claims-are-committed-claimlock-is-a-per-clone-cache)
  - [Gate a change in CI](#gate-a-change-in-ci)
  - [Hand a re-check to someone](#hand-a-re-check-to-someone)
  - [After a merge](#after-a-merge)
  - [Who verified](#who-verified)
  - [`unanchored`](#unanchored)
- [Hooks](#hooks)
- [Skills](#skills)
- [How staleness works](#how-staleness-works)
- [CI](#ci)
- [Migrating from earlier claimlock](#migrating-from-earlier-claimlock)
- [Limits](#limits)

## Install

```
/plugin marketplace add ncx-ai/claimlock
/plugin install claimlock@claimlock
```

The repository is private: adding the marketplace needs read access to
`ncx-ai/claimlock` on GitHub.

Requires Python ≥ 3.11 (standard library only — nothing to `pip install`).
On an older `python3` the CLI exits 2 with a message, and the hooks stay
silent and log one line to `hook-errors.log` rather than fail.

Git is optional, but a team needs it. Inside a git work tree pins are git's
normalized blobs (so LF and CRLF clones agree), pins are checked for being
anchored in history, `diff` reads prior content from git, and `check
--changed`, `who` and `resolve` work from history. In a plain directory the
CLI still works: pins hash raw bytes, anchoring is not evaluated, `diff`
cannot show prior content, `who` reports `unknown`, and `check --changed`
exits 2.

## Thirty-second tour

```
claimlock init                        # .claimlock.toml, claims/, .gitignore and .gitattributes entries
claimlock new retries-are-capped --area core
# edit claims/retries-are-capped.md: write the claim, cite evidence and sources
claimlock verify retries-are-capped   # pins every cited source, marks it verified
git add -A && git commit -m "claim: retries are capped"

# time passes; a cited source changes...
claimlock check                       # STALE retries-are-capped — exit 1

claimlock diff retries-are-capped     # see exactly what changed since verification
```

## Statuses and states

A claim's **status** is written in its file:

| Status | Means | Fails `check`? |
|---|---|---|
| `unverified` | Written, not yet checked. The default for a new claim; nothing is pinned. | No |
| `verified` | Checked against its evidence; `claimlock verify` pinned every source. Gets a state (below). | When not `fresh` |
| `owed` | Its re-check is handed to the person in `owed_by` (by `owe`, or by `resolve` after a merge). Pins are kept so `diff` can show what moved. | No (listed) |
| `refuted` | Checked and found false; kept as a record. `verify` refuses it. | No |

A verified claim's **state** is computed on every run, per source; the worst source wins
(`missing` > `renamed` > `stale` > `unanchored` > `unpinned` > `fresh`):

| State | Means |
|---|---|
| `fresh` | Every source's content equals its pin, and (inside git) that content is committed or staged. |
| `unpinned` | A source has no pin — usually added by hand after the last `verify`. |
| `unanchored` | Content matches its pin but was never committed or staged, so other clones can't recover it. |
| `stale` | A source's content changed since it was pinned. Run `claimlock diff <id>`. |
| `missing` | A source doesn't exist or can't be read. |
| `renamed` | A source was moved (committed or staged); `check` names the new path. Run `claimlock follow <id>`. |

Two **problems** fail `check` whatever the status:

| Problem | Means |
|---|---|
| `invalid` | The file breaks a format rule ([docs/format.md](docs/format.md)) — including a `pins:` digest that no longer matches the sources. |
| `conflicted` | The file holds git conflict markers. Run `claimlock resolve`. |

## Commands

| Command | Does |
|---|---|
| `claimlock init` | Create `.claimlock.toml` and `claims/`, add `.claimlock/` to `.gitignore`, and add `claims/*.md text eol=lf` to `.gitattributes`. |
| `claimlock new` | Scaffold an unverified claim (`claimlock new <id> --area <area>`). |
| `claimlock check` | The gate: exit 1 if any claim is invalid (including conflicted), or verified and `stale`, `missing`, `renamed`, `unpinned` or `unanchored`. `owed` claims are listed and never fail it. `--changed <base>` blocks only on claims whose sources or claim file changed in committed history since the merge base with `<base>` — see [Using claimlock as a team](#using-claimlock-as-a-team). Prints a bounded report — `--full` for every claim and source. |
| `claimlock stale` | List non-fresh verified claims (exit 1 if any) and `owed` claims, tab-separated. `--owed-by <email>` / `--mine` list only claims owed by that person. |
| `claimlock list` | List every claim with its status, flags and headline. `--status <s>`, `--owed-by <email>`, `--mine` filter it. A long headline is cut to 120 characters — `--full` prints it whole. |
| `claimlock search` | Ranked search — ask it a question in your own words. Scores every claim's id, headline, area, source paths, body and evidence refs (BM25) and prints the best matches first, one line per hit (id, area, status, headline); `--top N` bounds the list (default 10, naming the command to see the rest); `--body` restores the matching body lines indented under each hit; an empty result means no claim covers this, not that the search failed — see [docs/format.md](docs/format.md) for the relevance floor. `--literal` restores the old case-insensitive substring match, for an exact path or string. |
| `claimlock show` | One claim in full: status (and owner, if owed), freshness per source, who verified each pin, evidence, body. The body is capped at 40 lines and each evidence `ref` at 200 characters — `--full` prints both whole. |
| `claimlock verify` | Re-hash every source (cache bypassed), pin it, write the `pins:` digest of the whole pin set, and mark the claim verified — clearing `owed_by`/`owed_since`. Refuses a conflicted, refuted or incomplete claim. |
| `claimlock follow` | Rewrite the path of each renamed source (reported by `check` as `renamed`) to its new path, keeping its pins; the claim then reads fresh if the content is unchanged. |
| `claimlock owe` | Hand off a claim's re-check to someone (`--to <email>`, default your git `user.email`; `--reason "<one line>"`); status becomes `owed`. |
| `claimlock resolve` | Settle conflicted `sources` pins after a merge: keep a pin only when it equals the merged content, else mark the claim owed by the merger; leave every other conflict for a person. |
| `claimlock diff` | Show what changed in a verified or owed claim's sources since it was pinned, reading the pinned content from git; line by line, so a change of line endings alone is reported as such. Each source's unified diff is capped at 200 lines — `--full` prints it whole. |
| `claimlock who` | Who verified each of a claim's pins, from git history (email, timestamp, commit), tab-separated. |
| `claimlock refs` | Fail if any `` Claim: `id` `` marker in prose names no claim. Its census line also reports how many claims no prose cites at all (`--orphans` lists them, capped like every other listing, `--full` to see all) — an uncited claim never fails the gate; it's a documentation gap, not a false statement. |
| `claimlock affected` | List claims whose sources include the given path(s). |
| `claimlock import` | Import claims from the original (unpinned) ground-truth format. |
| `claimlock self-test` | Prove the freshness/anchoring/dangling-marker detectors actually fire, on this machine. |
| `claimlock hook` | The Claude Code hook entry point (`claimlock hook <event>`); always exits 0. |

Every command accepts `-C <dir>` to run as though started in `<dir>` — but
`-C` is an option of `claimlock` itself, not of the subcommand, so it must
come **before** the subcommand name: `claimlock -C <dir> check` works,
`claimlock check -C <dir>` errors (`unrecognized arguments: -C <dir>`). Full
field-level and format detail: [`docs/format.md`](docs/format.md).

## Output size

Every command prints a bounded report; `--full` restores the complete output.
`search` is bounded a different way — ranked and capped at `--top` (default
10) regardless of store size; `--body` still shows matching body lines under
each hit but stays capped at whichever mode applies (`--top` under ranking,
uncapped under `--literal`, which is what restores its old substring
behaviour). Measured at `228f748` against two stores — **A**: 41 real claims,
every one failing; **B**: 201 claims, 54 stale. `search`'s own row is measured
separately below the table, on a generated fixture, because it depends on
the query and the cap rather than store size the way the other rows do.
Tokens are bytes/4.

| Command | ~tokens | with `--full` |
|---|---:|---:|
| `check` | 1,055 (A) / 295 (B) | 2,496 (A) |
| `check --json` | 6,552 (A) / 4,270 (B) | 15,841 (B) |
| `search <question>` (ranked, `--top` default 10) | 311 | see below |
| `show <id>` (largest real claim) | 2,024 | 4,047 |
| `diff <id>` (large file rewritten) | 669 | 11,116 |
| `list` | 1,491 (A) | — |
| `stale` | 1,912 (A) | — |
| `affected <path>` | 345 (B) | — |
| `refs` | 13 | — |
| SessionStart hook | 110 | capped at 2,000 characters |

`search`'s size no longer scales with store size the way A/B did for the
other rows above — measured 2026-09-16 on a fresh 30-claim fixture where
every claim has 8 body lines matching the query `needle` (the same fixture
`tests/test_output_budget.py::SearchBudgetCeiling` asserts against, so this
number is reproducible, not a one-off): ranked default (`--top` 10) **311**
tokens, ranked `--body` **1,949**. `--literal` restores the old uncapped
substring match: **892** tokens by default, **5,805** with `--body`, on the
same 30-claim fixture — uncapped, and every claim matched here, so this is
close to a worst case for it.

Loaded or read, not printed: the two claimlock skills ~5,405 tokens when
invoked; `README.md` ~9,688 and `docs/format.md` ~15,022 **if read** — they are
reference for changing claimlock itself, not for routine claim work.

The cheap sequence, in the order you work: `claimlock affected <paths>` while
editing, one `claimlock check --changed <base>` before committing, and
`claimlock diff <id>` only for the claim you are about to verify. Add `--full`
when a bounded report cut something you actually need; the cut always names
what it withheld.

## Regions

A source can pin a marker-delimited **region** of a file instead of the whole
file, so an edit elsewhere in that file doesn't stale the claim. Wrap the part
that matters in a `claimlock:begin <name>` / `claimlock:end <name>` pair, in
whatever comment syntax the file uses:

```python
# claimlock:begin retry-cap
def clamp(requested, max_timeout):
    return min(requested, max_timeout)
# claimlock:end retry-cap
```

```javascript
// claimlock:begin retry-cap
function clamp(requested, maxTimeout) {
  return Math.min(requested, maxTimeout);
}
// claimlock:end retry-cap
```

The name matches `^[a-z0-9][a-z0-9-]*$`. The pinned region is the lines
**strictly between** the two marker lines, so restyling the marker comments
themselves never changes the pin. Cite it in `sources` with a `region`:

```yaml
sources:
  - path: src/retry.py
    region: retry-cap
```

`claimlock verify` fills in `blob` (the whole file's pin, for anchoring and
`diff` — the staged file's blob when it holds the same region, else the
working tree's) and `hash` (the region's own pin) alongside it. Every per-source
listing — `check`, `stale`, `show`, `diff`, `--json` — names a region source
by its **key**, `path#region` (e.g. `src/retry.py#retry-cap`).

**Prefer a region** when the file that enforces a claim is large or shared —
a config module, a router, a file several claims already cite — and the
claim only depends on a small part of it. A whole-file pin on such a file
goes stale on every unrelated edit; a region pin only goes stale when the
marked lines change.

A region that cannot be extracted unambiguously reads `missing` (`diff` and
`show` print the reason): the marker is absent, duplicated (a second `begin`
or an orphan second `end`), unterminated (a `begin` with no matching `end`),
or out of order (an `end` before any `begin`) — or the file isn't valid
UTF-8, so it can't be scanned for markers at all.

## Using claimlock as a team

### Claims are committed; `.claimlock/` is a per-clone cache

```
git add claims/ src/limit.py && git commit -m "retry cap, and a claim for it"
```

A claim file travels like any other tracked file: push, pull, merge. Re-verifying
the same claim against the same content on two branches produces identical files,
so they merge without a conflict. `init` writes `claims/*.md text eol=lf` to
`.gitattributes` so claim files stay LF in every clone (a CRLF claim file is still
accepted and read as LF).

### Gate a change in CI

```
claimlock self-test && claimlock check --changed origin/main && claimlock refs
```

`check --changed <base>` takes the merge base of `<base>` and `HEAD` and puts a
claim **in scope** when one of its cited sources, or its own claim file, changed
in `merge-base..HEAD`. Only in-scope claims block (exit 1): invalid, conflicted,
`stale`, `missing`, `unpinned` or `unanchored`. So a change blocks only on claims
it touched:

```
STALE    retries-are-capped
         src/limit.py: stale
pre-existing (not changed here):
  old-claim: stale
hints:
  stale: re-check it (claimlock diff retries-are-capped), then: claimlock verify retries-are-capped
claimlock: 2 claims (1 in scope), 3 sources hashed — 0 invalid, 0 unpinned, 0 unanchored, 1 stale, 0 missing
```

Drift the change did not touch is listed under `pre-existing (not changed here)`
and does not block. `owed` claims are listed (`OWED     <id> → <email> since
<commit>, N commits ago`) and never block, scoped or not.

The scope is **committed changes only** — an uncommitted edit is not in it. Run
plain `claimlock check` to gate the whole working tree (locally, or in a
pre-commit hook). `--changed` exits 2 outside a git repository, when git cannot
find a merge base with `<base>` (in CI, the base branch must be fetched with
enough history to reach it), or when git fails to list the changes since that
merge base — an exit 2 is never a pass.

### Hand a re-check to someone

```
claimlock owe retries-are-capped --to amy@example.com --reason "MAX moved into config"
```

When your change stales a claim you cannot re-check yourself, `owe` writes the
hand-off into the claim file for you to commit with the change: `status: owed`,
`owed_by: <email>`, `owed_since: <HEAD's short id>` (or `none` outside git), and,
with `--reason`, a body line `Owed <date> by <your git email>: <reason>`. The pins
are left as they were. `--to` defaults to your own `git config user.email`; with
neither, `owe` exits 2.

`owe` refuses (exit 1) a claim with problems, an `unverified` or `refuted` claim,
a verified claim that is `fresh` (nothing is owed), a claim already owed to that
same person, a `--to` that is not an email address (or, without `--to`, a git
`user.email` that is not one), and a multi-line `--reason`.
Owing an owed claim to a different person resets `owed_since` — a new hand-off
starts a new age.

A hand-off is not a way past the gate: the claim stays visibly unverified. `check`
lists it, SessionStart tells its owner, and it stays owed until someone re-checks
it and runs `claimlock verify`, which sets `status: verified` and removes
`owed_by`/`owed_since`. To find the work:

```
claimlock stale --mine                          # owed to your git user.email
claimlock list --owed-by amy@example.com
```

An owed claim keeps its pins, so `claimlock diff <id>` still shows what moved
since it was last verified, and `claimlock show <id>` lists each source's state —
both read the pins as if the claim were verified. `check` gives an owed claim no
verdict and never fails on it; a non-fresh one's `OWED` line ends with its worst
source state, e.g. `(stale)` or `(missing)`.

Emails are compared case-insensitively (and ignoring surrounding spaces) wherever
claimlock asks whether a claim is owed to someone — `owe`, `--mine`, `--owed-by`
and the hooks — while `owed_by` keeps the case it was written with.

### After a merge

```
claimlock resolve
```

Two branches that verified the same claim against **different** content conflict
on its `blob:` lines. Resolve the conflicts in the source files first, then run
`resolve` (no ids: every conflicted claim). It settles only conflicts inside the
frontmatter `sources` block, by re-hashing each cited source's merged working-tree
content:

| Outcome | When | Result |
|---|---|---|
| `KEPT` | every source equals one side's pin | the claim keeps those pins; exit 0 |
| `OWED` | some source equals neither side's pin | matching pins are kept, the rest keep "ours", and the claim becomes `owed` by your git email since `HEAD`; exit 0 |
| `LEFT` | a conflict anywhere else (body, evidence, another field, a frontmatter delimiter), the two sides cite different sources, or an `OWED` case with no git `user.email` or one that is not an email address | the file is untouched; exit 1 |

`resolve` never stages or commits. Until a claim is resolved, `check` fails it as
`INVALID` with ``<file>: contains git conflict markers — run `claimlock resolve` ``,
and `verify` refuses it.

### Who verified

```
claimlock who retries-are-capped
src/limit.py	amy@example.com	2026-09-14T10:00:00-04:00	0ed1471
```

Read from git history: the latest commit that added or removed that exact pin
line in the claim file, following renames of the claim file. A pin no commit has
added reads `uncommitted`; outside git, `unknown`; a source with no pin,
`unpinned`. `claimlock show <id>` prints the same beside each source. Use it to
route a stale claim to the person who last vouched for it.

### `unanchored`

Inside git, a pin is **anchored** when its content appears at that exact path in
any commit reachable from any ref (full history, local branches and stash
included) or is staged for that path (any index stage, so a conflicted merge
counts). A verified source whose pin is neither reads `unanchored`, which fails
`check`: no other clone could ever see or `diff` that content.

The usual cause is running `verify` after editing a source and before `git add`.
Once the source is staged the pin is anchored, so a pre-commit `check` after
`git add` reads fresh. A pin anchored only by your unpushed branch or stash reads
`stale` in CI (CI's checkout does not contain that content) and blocks there.
Sources git ignores are exempt, and anchoring is
not evaluated at all when the store root lies inside a directory an enclosing
repository ignores, or outside git.

## Hooks

| Hook | Who sees it | When |
|---|---|---|
| SessionStart | Claude (as context) | First, the claims owed to your git `user.email`, if any. Then counts of invalid, conflicted, unpinned, unanchored, stale, missing and owed claims and dangling markers, the areas they're in, and a reminder to search before asserting. |
| PostToolUse (after Bash / MCP tool calls) | Claude (as context) | Only when HEAD moved since the last check. Claims that became owed to you in that range and claim files with merge conflicts ("run `claimlock resolve`") come first; then now-non-fresh claims backed by files changed in the range, each naming the newest commit in the range that changed its source — author email (mailmap-aware) and subject (truncated); then dangling markers. |
| PostToolUse (after Edit / Write / MultiEdit / NotebookEdit) | Claude (as context) | At the moment of the edit: which `verified`/`owed` claims cite the file just edited, so the check can happen while the change is fresh instead of after the fact. Silent when nothing edited resolves to an in-root, non-claims path, or when nothing cited was found. |
| Stop | The user (a `systemMessage`) | Problems new *since the last check* in this clone (not pre-existing ones) — claims that became invalid, conflicted, unpinned, unanchored, stale, missing or owed, and dangling markers — separating drift from your uncommitted edits to cited sources from drift that arrived another way (a `git pull`, a tool), plus any HEAD movement. A claim the HEAD-moved report already names in the same state is not listed twice, and anything the message could not show is reported again at the next Stop. |

The edit-time notice never hashes and spawns no git — an edited file's claims
are presumed drifted the moment it's touched, since computing freshness would
add cost to answer a question the notice doesn't ask. Each file is named at
most once per session: editing it ten times says it once, and a fresh session
sees it again.

With a team, a few details matter. The email for "owed to you" is read when the
session starts; with no git `user.email` then, owed-to-you notices stay silent for
that session. While a merge, rebase or cherry-pick is stopped mid-way, Stop does
not label any drift as your uncommitted edits. A `git pull` that stops on
conflicts does not move HEAD, so its conflicted claims reach you through Stop
(``1 claim became conflicted (<id>) — run `claimlock resolve` ``), not PostToolUse.

Hooks **never block**: they always exit 0 — including under a `python3` older
than 3.11, where they print nothing and log one line to `hook-errors.log` in
the plugin data directory — never set `decision`, and a Stop
warning does not continue the turn — it is shown to the user only, after
Claude has already finished responding. Hooks are active only in a project
that has a `.claimlock.toml` — in the project directory Claude Code opened, or
one of its ancestors (a config in a *subdirectory* of the opened project is not
seen). Anywhere else, including a repository that merely has a `claims/`
directory, every hook prints nothing at all and runs no git command.

Hooks keep per-session state (`sessions/<session>.json` and a `.lock`) in the
plugin data directory, or in `.claimlock/` when no data directory is set. At
SessionStart, the files of any other session idle for more than 7 days are
removed (never while that session holds its lock), so they do not pile up.
`hook-errors.log` is moved to `hook-errors.log.1` once it passes 1 MiB.

## Skills

| Skill | Purpose |
|---|---|
| `using-claimlock` | How to read before asserting and write after establishing — searching claims before stating a fact, registering one after proving it, and what to do when your change stales someone else's claim. |
| `operating-claimlock` | Adopting claimlock in a repo, wiring the scoped gate into CI, resolving claims after a merge, importing an older claim store, and triaging a pile of stale or owed claims. |
| `design-lenses` | Ten independent lenses (correctness, scale, concurrency, fairness, falsifiability, adversarial, cost, consumer experience, operability, claim integrity) for judging when work is actually done. Fairness is conditional on independent parties sharing a resource; the rest always apply. |
| `evidence-standards` | What makes a green result meaningful — falsifiable checks, non-zero pass counts, sentineled instruments — versus a mechanism that looks like enforcement and enforces nothing. |

## How staleness works

Each pinned source records a **git blob id**:

- **Inside a git work tree** it is the blob git would store for the file,
  computed by one `git hash-object --stdin-paths` over the sources, so clean
  filters and `text`/`eol`/`core.autocrlf` normalization apply. An LF checkout
  and a CRLF checkout of the same commit get the same pin.
- **Outside git** (or if that git call fails) it is `sha1("blob <len>\0" +
  bytes)` over the raw bytes — `git hash-object --no-filters`. A store created
  in a plain directory stays valid after `git init` for every file git applies
  no conversion to.

A **region** source ([Regions](#regions)) is pinned the same way, applied to
the region's text instead of the whole file: `sha1("blob <len>\0" + text)`,
where `text` is the lines strictly between the markers, decoded as UTF-8 with
a trailing `\r` stripped from each line — git's clean filters never run on
that extracted text, only this line-ending normalization does. Inside git, a
region pin still needs anchoring, and the check has a fallback: it first asks
whether the whole file's blob is anchored (as for a whole-file source), and
only when it isn't does it read the file's currently **staged** content
straight from git and check whether that still contains the same region with
the same hash. That second check is what keeps a region pin anchored right
after `verify` and `git add` on the file it came from — without it, an
uncommitted edit anywhere else in the same file would leave the region pin
`unanchored` until the whole file was committed.

That's deliberate:

- **Not timestamps.** An mtime survives a checkout, a copy, or an editor
  touching the file without changing it, and can go backward across a branch
  switch. Content hashing can't be fooled either way: same content, same pin,
  regardless of when it was written.
- **`diff` needs the old content**, not just a mismatch. It reads the pinned
  blob from git (`git cat-file blob <sha>`) and nowhere else. When git doesn't
  have it — no repository, or content that was never committed — `diff` says the
  prior content is unavailable. That is why an `unanchored` pin fails `check`:
  a pin other clones cannot recover cannot be re-checked by them.
- **A stat cache avoids re-hashing unchanged files** on every run
  (`.claimlock/cache/stat.json`, keyed by `(size, mtime_ns)` and by hashing
  mode, so a clone that gains or loses git never reuses the other mode's
  entries). It has a 2-second *racy-timestamp guard*, the same one git uses: an
  entry whose mtime is under 2 seconds old is never cached, so a same-size edit
  within one filesystem clock tick can't be missed by trusting a stale cache
  entry. `verify` and `resolve` always bypass the cache.
  **The trade-off:** for `check`, hooks and listings, an entry at least 2
  seconds old is trusted whenever the file has the same size and the same
  `mtime_ns`, without re-reading it. A tool that rewrites content but restores
  the timestamp — `cp -p`, `rsync -a`, a build cache that restores mtimes — can
  therefore hide a same-size edit from a warm cache. A fresh clone or a CI run
  has no cache and always hashes; deleting `.claimlock/cache/` forces the same
  locally. Inside git an entry is also tied to the settings that decide how git
  converts the file — the repository's `config`, `config.worktree` and
  `info/attributes`, the global and system config and attributes files at their
  default locations (or where `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` point),
  the git config environment variables, and every `.gitattributes` from the work
  tree top down to the file's directory. Changing any of them (by content, not
  timestamp) re-hashes the affected files on the next run instead of trusting a
  hash made under the old rules. Not covered: a file pulled in by a config
  `include`, a custom `core.attributesFile` edited in place (changing the
  setting itself is covered), a system config outside `/etc` (Homebrew's or
  Git for Windows' own `etc/gitconfig`), and a repository located by
  `GIT_DIR` rather than a `.git` above the project; after editing one of
  those, delete `.claimlock/cache/`.

## CI

```
claimlock self-test && claimlock check --changed origin/main && claimlock refs
```

`self-test` proves the detectors can actually fire on this machine before
trusting `check`/`refs` to mean anything; `check --changed` is the
freshness/validity gate scoped to what the change touched (plain `claimlock
check` gates the whole store); `refs` fails on any prose marker naming no claim.

The CI checkout must fetch the base branch with enough history for `git
merge-base` to find the common ancestor — in GitHub Actions, `fetch-depth: 0` on
`actions/checkout` — or `check --changed` cannot compute its scope and exits 2.
It also exits 2 if git fails while listing the changes; exit 2 is never a pass.

## Migrating from earlier claimlock

- **`verified_at`** is accepted and ignored; the next `verify` of that claim
  removes the line. Who verified a pin and when now comes from git (`claimlock
  who`).
- **`.claimlock/objects/`** is no longer read or written; delete it.
- **Pins of files git converts** (line endings, clean filters) were raw-byte
  hashes and now read `stale` once, which fails safe. One `verify` settles them
  for every clone.
- **`pins:`** — a claim verified before the pin-set digest existed has no
  `pins:` line and stays valid; its next `verify` adds one. A hand edit that
  already changed its `sources` (other than reordering them) is not caught
  until then.
- **Upgrade everyone together, CI included.** An older claimlock reports
  `unknown field 'pins'` on every claim this version verified, and its
  `verify` leaves a stale `pins:` line that this version reads as invalid.
- **`check --json`'s `results`** now holds blocking claims (invalid, or a
  non-fresh state, in scope), plus failing claims outside the `--changed`
  scope, plus `owed` claims — not every claim, and a result's `blocking`
  field can therefore be `false`. A new `omitted` gives the count left out.
  A consumer that parsed every claim from `results` needs `--full`, which
  restores the old shape (`"omitted": 0`).
- **`search`'s default output** is one line per hit (id, area, status,
  headline), not the matching body lines indented under each hit — a consumer
  that parsed those lines needs `--body`, which restores the old shape.
  `show`'s body and evidence `ref`s are now capped (40 lines, 200 characters)
  by default — `--full` restores them whole. `list`'s headline is capped at
  120 characters — `--full` restores it whole.

## Limits

- **File-level granularity, unless you pin a region.** A whole-file pin covers
  the whole file's content; a whitespace reformat or an unrelated edit
  elsewhere in a large shared file makes every claim citing it stale, whether
  or not the cited behavior changed. Prefer the narrowest file that actually
  enforces the behavior when writing `sources`, or pin a [region](#regions) of
  it.
- **Marker drift.** A region pin only knows the lines between its markers; a
  refactor that moves the enforcing code out from between them (or moves other
  code in) leaves a `fresh` region that no longer holds what the claim is
  about. Nothing catches this automatically — review it, the same way you'd
  review any other diff to a cited source.
- **An unstaged plain `mv` is not detected.** Renaming a source with a plain
  `mv` — not `git mv`, and not staged with `git add` afterward — is invisible
  to git (it shows as a deletion plus an untracked file), so the source still
  reads `missing`, not `renamed`. Stage the move (`git add -A` covers it) and
  it's found.
- **Clean filters don't apply to regions.** A region's pin is computed from
  the file's raw bytes with only line-ending normalization applied, never a
  `.gitattributes` clean filter — unlike a whole-file pin, which is exactly
  what git would store. Only matters for a file with a clean filter configured.
- **A file that isn't UTF-8 can't have regions.** Marker scanning decodes the
  file as UTF-8; a source with a `region` on a non-UTF-8 file always reads
  `missing`, with the reason `not UTF-8, so regions cannot be read`. A
  whole-file pin on the same file has no such restriction.
- **Line endings, only outside git or when clones convert differently.** Inside
  git a line-ending difference git normalizes does not change a pin. A pin still
  differs between clones outside git (raw bytes), and when git would convert a
  file differently in two clones — for example content committed with CRLF bytes,
  checked out in one clone with `core.autocrlf=true` and in another without it.
  That fails safe (stale, never a false fresh) but does not settle; commit a
  `.gitattributes` rule for such files so every clone converts them the same way.
- **`resolve` handles pins only.** It settles conflicts inside the `sources`
  block. A conflict in the body, the evidence or any other field, or two sides
  citing different sources, is left in the file for a person (exit 1).
- **Conflict detection scans the whole claim file.** A claim body that quotes
  both a `<<<<<<< ` line and a `>>>>>>> ` line — inside a code fence, say — reads
  as conflicted.
- **Two re-verifications of one claim conflict, or read invalid.** `verify`
  writes a `pins:` digest of the claim's whole pin set, so a branch that
  re-verifies a claim after changing its first source and another that
  re-verifies it after changing its second conflict on that line, even though
  their `blob` lines would merge cleanly — otherwise the merge would read fresh
  for a combination of contents no single verification covered. (If one branch
  used a claimlock without the digest, the lines can merge cleanly instead, and
  the merged digest then fails to match: the claim is `invalid`.) `resolve`
  keeps a side only when the merged content is exactly that side's whole pin
  set, read from that side's own version of the claim; the combination case
  becomes `owed`. Re-verifying identical content on both branches writes
  identical lines and does not conflict. Hand edits to `sources` other than
  reordering make the claim invalid until it is re-verified.
- **`who` and `show` attribute by pin line.** A hand edit that only reorders
  `sources:` removes and re-adds pin lines, so it credits the person who
  reordered them. A claim file committed with CRLF line endings (no
  `.gitattributes` rule) reports every pin `uncommitted`.
- **Local-only refs anchor.** Your unpushed branches, your stash and a
  conflicted merge's index stages all count as anchors, so a pin can read fresh
  in your clone on content only your clone has. In CI it reads `stale` (CI's
  checkout does not contain that content).
- **A symlinked source is anchored at its target.** A pin hashes the content
  reached by following links — the source itself being a symlink, or a directory
  above it (`link/a.py` where `link` → `real`) — while git stores a symlink as
  its link text, so no commit holds that content at the cited path. Anchoring
  therefore also looks up the fully resolved path (for a regular file inside the
  project root). Changing where a link points is a content change at the target,
  not at the link; citing the resolved path directly is still clearer.
- **Source paths starting with `:`.** The ignore check reads a leading `:` as
  git pathspec syntax, so a gitignored source named `:x` is not recognised as
  ignored and reads `unanchored`. Avoid such filenames.
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
  `_stat_marks`/`head_check`) gates on the raw `os.stat().st_mtime_ns` values
  of git's `HEAD`, current-ref and reflog files, recorded at the last probe,
  with **no** racy-timestamp guard — that guard belongs to a different
  mechanism (the content stat cache above). On a filesystem with
  second-or-coarser mtime resolution, a commit landing within the same tick as
  the last probe can leave those values looking unchanged, so the hook doesn't
  notice it that turn. Nothing is lost, only delayed: the comparison keeps
  failing to match until a later tick's stat can tell the two apart, at which
  point the report covers the whole range since the last one actually seen —
  the same reason a transient git failure mid-check also only delays rather
  than drops commits (the marks are restated and moved forward, but the last
  known HEAD is held onto until git succeeds again). `claimlock check` run
  directly is unaffected, since it always re-evaluates from scratch.
- **Marker scanning outside git walks the whole tree.** Inside a git work
  tree, `claimlock refs` and the SessionStart/Stop hooks take candidate files
  from one `git ls-files --cached --others --exclude-standard`, so gitignored
  trees (`target/`, `build/`, `vendor/`…) cost nothing — and markers in them,
  or inside git submodules, are not scanned. Outside git, if that git call
  fails, or when the store root itself lies in an ignored directory, every
  non-hidden directory except `node_modules/` and the claims directory is
  walked, so a large untracked tree makes each Stop slower.
- **Lock files accumulate.** The per-session hook lock
  (`<plugin data dir>/sessions/<session-id>.lock`) is left in place after use
  rather than removed — harmless (an empty file, reused by session id) but it
  means the `sessions/` directory grows by one file per distinct session ever
  seen, never shrinking on its own.
- **Everything here is file-level and content-level, never semantic.**
  claimlock cannot tell whether a change to a cited file actually invalidates
  the claim's text — it only tells you the content changed and the claim is
  owed a look. That look is the point: see `claimlock diff` and the
  `using-claimlock` skill.
