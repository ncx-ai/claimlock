---
name: operating-claimlock
description: Use when adding claimlock to a repository, wiring its gate into CI or a pre-commit hook, resolving claim files after a merge, importing an existing claim directory, triaging many stale or owed claims at once, or interpreting claimlock hook output and noise
---

# Operating claimlock

## Adopt

    claimlock init            # .claimlock.toml, claims/, .gitignore and .gitattributes entries
    claimlock self-test       # prove the detectors fire on this machine

Commit `claims/`; `.claimlock/` is a per-clone cache, gitignored. Git is
optional, but a team needs it — inside git, pins are git's normalized blobs
anchored in history, and `check --changed`, `who` and `resolve` read it;
outside git, pins hash raw bytes and those commands can't help.

`.claimlock.toml` keys (all optional): `claims_dir`, `marker_globs`,
`marker_pattern`. Unknown keys are an error — a typo must not silently fall
back to a default.

## Work cheaply

The default sequence for routine claim work, every time:

1. After editing files: `claimlock affected <paths>` — lists only the claims
   citing those paths.
2. Before committing, once: `claimlock check --changed <base>`.
3. `claimlock diff <id>` only for the claim you are about to verify next.
4. `claimlock search <topic>` reads one line per hit; add `--body` only when
   the headline isn't enough.

**Do not read `docs/format.md` or `README.md` for routine claim work** —
they are reference for changing claimlock itself (~11,200 and ~8,500 tokens);
these skills carry what these flows need. **Do not run `check --json`**
unless a machine is parsing it — the text form is smaller.

## Gate

CI, on a pull request:

    claimlock self-test && claimlock check --changed origin/main && claimlock refs

Locally, or in a pre-commit hook after `git add`, gate the whole working tree:

    claimlock check

`check --changed <base>` blocks only on claims **in scope** — a cited source or
the claim file itself changed in committed history since the merge base with
`<base>`. Read its output in three parts:

- claims printed as `INVALID`, `STALE`, `MISSING`, `RENAMED`, `UNPINNED` or
  `UNANCHORED` are in scope and block (exit 1);
- `pre-existing (not changed here):` lists drift the change did not touch — it
  does not block, and it is still owed a re-check by someone;
- `OWED <id> → <email> since <commit>` lines never block.

It sees committed changes only; uncommitted edits need plain `check`. It exits
2 outside git, with no merge base with `<base>`, or if git fails to list
changes since it — fetch the base branch with enough history in CI (e.g.
`fetch-depth: 0` in GitHub Actions). An exit 2 is never a pass.

Exit codes: 0 clean, 1 findings, 2 the store couldn't be read (no claims
directory, or a bad `.claimlock.toml`) or `--changed` couldn't run — exit 2
prints only an error. Otherwise `check` ends with a summary line
`claimlock: N claims, M sources hashed — …` (with `--changed`,
`claimlock: N claims (K in scope), M sources hashed — …`), and `check --json`
carries `claims`, `sources_hashed`, `scope` and per-claim `in_scope`/`blocking`.
N=0 in a repo you believe has claims means the gate is pointed at the wrong
directory — a gate failure, not a pass.

A pre-commit `check` after `git add` is fresh for newly verified content — the
staged blob anchors the pin. Without `git add`, an edited-then-verified source
reads `unanchored`. A pin anchored only by an unpushed branch or a stash reads
`stale` in CI (its checkout lacks that content) and blocks there.

In CI, install claimlock from wherever your team actually gets it; if you
don't know the install source, say so and leave a placeholder — never invent
a package name.

## Import an older store

    claimlock import <old-claims-dir>

Import keeps each claim's `status` and drops every pin. A `verified` claim
arrives **unpinned** and `check` fails until it's re-checked and verified;
imported `unverified`/`refuted` claims have nothing to pin and fail `check`
only if invalid. That's the point — import must not launder old
verifications into fresh pins. Plan the re-check as work; do not bulk-verify
to get green.

**A red gate after import is the correct result, and it is what you report.**
The old store never recorded *which content* was reviewed, so no earlier
review — however recent, however trusted — can be carried onto today's
files. If told to carry the verified status over, or that the gate must be
green before the session ends, do not `verify` claims you haven't re-checked
in this session: explain that the gate is red because the re-check is owed,
and list the claims that need it.

## Triage many stale or owed claims

    claimlock stale                 # id, area, state, changed paths — and owed claims
    claimlock stale --mine          # only claims owed to your git user.email
    claimlock list --owed-by <email>
    claimlock affected <path>...    # which claims a file backs
    claimlock diff <id>
    claimlock who <id>              # who verified each pin, and in which commit

Group by changed path — one refactor usually stales a cluster. Re-check each
claim against its enforcement site; verify only the ones you re-checked. For
one you cannot re-check, route it to the person `who` names, or record the
hand-off with `claimlock owe <id> --to <email> --reason "…"`. A large shared
file stales every claim citing it — prefer the narrowest file (or a `region`
inside it — regions: see the `using-claimlock` skill) that actually enforces
the behaviour when writing `sources`. A
`RENAMED` claim isn't drift to route to anyone: run `claimlock follow <id>` to
rewrite its path and keep its pins.

## After a merge

Two branches verifying the same claim against different content conflict on
its `pins:` line (and any `blob` line both changed); `check` then fails it as
`INVALID … contains git conflict markers`. Resolve the source files first, then:

    claimlock resolve

- `KEPT` — the merged content is exactly what one side verified; that side's pins stay.
- `OWED` — some source matches neither side, or the sources match pins from
  different sides (a combination nobody verified); the claim becomes `owed` by
  your git `user.email`, to re-check.
- `LEFT` (exit 1, file untouched) — a conflict outside the `sources` block (body,
  evidence, another field), sides citing different sources, or an `OWED` case
  with no usable git `user.email`. A person edits those. `KEPT` needs no email.

`resolve` never stages or commits, and never picks a pin nobody verified — do
not hand-pick a `blob:` line, that records a check against content it may not
match.

## Across platforms

Inside git, line endings are handled — pins are git's normalized content, so
LF and CRLF checkouts of the same commit agree, and `init` keeps claim files
LF via `claims/*.md text eol=lf` in `.gitattributes`. A `.gitattributes` rule
is still needed for files git would convert differently across clones (e.g.
CRLF bytes committed, checked out with `core.autocrlf=true`) — else it reads
stale in one clone after verifying in another. Outside git, pins hash raw
bytes, so a line-ending change stales the claim.

## Hooks (installed with the plugin)

| Hook | Means | Do |
|---|---|---|
| Session start | Claims owed to Claude first, then drift counts by state, with affected areas | Search before asserting there; re-check what's owed |
| After a tool call (HEAD moved) | Newly-owed claims and conflicted files first, then claims the change staled | `claimlock resolve` the conflicts; re-check the rest before relying on them (`claimlock stale`/`refs` for the full list) |
| End of turn (user sees it) | Problems new since the last check, split from uncommitted-edit drift vs. other drift (e.g. a pull) | If the user relays it, answer with `claimlock diff <id>` |

Hooks exit 0 always and never block; active only when `.claimlock.toml`
exists in the opened project directory or an ancestor — elsewhere they print
nothing. Full message shapes, field ordering, caps, and session/log detail:
`docs/format.md`.

## Red flags

| Thought | Reality |
|---|---|
| "Import then verify everything so CI passes" | That converts unchecked beliefs into pins. Re-check first. |
| "The team already verified these last quarter" | Against content nobody recorded. Their review cannot be pinned to today's files. |
| "I was told to carry the status over" | Report the red gate and the re-check list instead. A green gate built on stamps is worse than a red one. |
| "`--changed` is green, so the store is healthy" | It ignores pre-existing drift by design. Run plain `claimlock check` and triage what it lists. |
| "Owe everything so CI goes green" | Each hand-off goes to a person who can re-check that claim, with a reason. A pile of owed claims nobody accepted is a red gate hidden. |
| "Take either side of the pin conflict" | Run `claimlock resolve`; it keeps pins only when the merged content is exactly what one side verified. |
| "0 claims, check passed" | Check `claims_dir`. Seeing nothing is not finding nothing. |
| "Disable the hook, it's noisy" | Noise means sources are too broad. Narrow them. |
| "I'll read format.md to be sure" | The skills carry every routine rule; format.md is reference for changing claimlock itself, and costs ~11k tokens. |
| "I'll run check after each edit" | Run `claimlock affected <paths>` while working and one `check --changed <base>` before committing. |
