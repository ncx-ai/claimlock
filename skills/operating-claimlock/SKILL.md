---
name: operating-claimlock
description: Use when adding claimlock to a repository, wiring its gate into CI or a pre-commit hook, resolving claim files after a merge, importing an existing claim directory, triaging many stale or owed claims at once, or interpreting claimlock hook output and noise
---

# Operating claimlock

## Adopt

    claimlock init            # .claimlock.toml, claims/, .gitignore and .gitattributes entries
    claimlock self-test       # prove the detectors fire on this machine

Commit `claims/`; `.claimlock/` is a per-clone cache and is gitignored. Git is
optional, but a team needs it: inside git, pins are git's normalized blobs and
must be anchored in history, and `check --changed`, `who` and `resolve` read
history. Outside git, pins hash raw bytes and those commands cannot help.

`.claimlock.toml` keys (all optional): `claims_dir`, `marker_globs`,
`marker_pattern`. Unknown keys are an error — a typo must not silently fall
back to a default.

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

It sees committed changes only; uncommitted edits need plain `check`. It exits 2
outside git, when no merge base with `<base>` exists, or when git fails to list
the changes since it — fetch the base branch with enough history in CI (e.g.
`fetch-depth: 0` in GitHub Actions). An exit 2 is never a pass.

Exit codes: 0 clean, 1 findings, 2 the store could not be read (no claims
directory, or a bad `.claimlock.toml`) or `--changed` could not run — exit 2
prints only an error. Otherwise `check` ends with a summary line
`claimlock: N claims, M sources hashed — …` (with `--changed`,
`claimlock: N claims (K in scope), M sources hashed — …`), and `check --json`
carries `claims`, `sources_hashed`, `scope` and per-claim `in_scope`/`blocking`.
If N is 0 in a repo you believe has claims, the gate is pointed at the wrong
directory — that is a failure of the gate, not a pass.

A pre-commit `check` after `git add` is fresh for newly verified content: the
staged blob anchors the pin. Without `git add`, a source edited and then verified
reads `unanchored`. A pin anchored only by an unpushed branch or a stash reads
`stale` in CI (CI's checkout does not contain that content) and blocks there.

In CI, install claimlock from wherever your team actually gets it. If you do not
know the install source, say so and leave a placeholder — do not invent a
package name.

## Import an older store

    claimlock import <old-claims-dir>

Import keeps each claim's `status` and drops every pin. Each claim that was
`verified` arrives **unpinned**, and `check` fails until it is re-checked and
verified. Imported `unverified` and `refuted` claims have nothing to pin and fail
`check` only if they are invalid. That is the point: an import must not launder old
verifications into fresh pins. Plan the re-check as work; do not bulk-verify
to get green.

**A red gate after import is the correct result, and it is what you report.**
The old store never recorded *which content* was reviewed, so no earlier review
— however recent, however trusted — can be carried onto today's files. If you
are told to carry the verified status over, or that the gate must be green
before the session ends, do not run `verify` on claims you have not re-checked
in this session: explain that the gate is red because the re-check is owed, and
list the claims that need it.

## Triage many stale or owed claims

    claimlock stale                 # id, area, state, changed paths — and owed claims
    claimlock stale --mine          # only claims owed to your git user.email
    claimlock list --owed-by <email>
    claimlock affected <path>...    # which claims a file backs
    claimlock diff <id>
    claimlock who <id>              # who verified each pin, and in which commit

Group by changed path: one refactor usually stales a cluster. Re-check each
claim against its enforcement site; verify only the ones you re-checked. For a
claim you cannot re-check, route it to the person `who` names: they re-check it,
or you record the hand-off with `claimlock owe <id> --to <email> --reason "…"`.
A large shared file stales every claim citing it — prefer the narrowest file
(or a `region` inside it — see the README) that actually enforces the
behaviour when writing `sources`. A `RENAMED` claim isn't drift to route to
anyone: run `claimlock follow <id>` to rewrite its path and keep its pins.

## After a merge

Two branches that verified the same claim against different content conflict on
its `pins:` line (and on `blob` lines both changed); `check` then fails the claim
as `INVALID … contains git conflict markers`. Resolve the source files first, then:

    claimlock resolve

- `KEPT` — the merged content is exactly what one side verified; that side's pins stay.
- `OWED` — some source matches neither side, or the sources match pins from
  different sides (a combination nobody verified); the claim becomes `owed` by
  your git `user.email`, to re-check.
- `LEFT` (exit 1, file untouched) — a conflict outside the `sources` block (body,
  evidence, another field), sides citing different sources, or an `OWED` case
  with no usable git `user.email`. A person edits those. `KEPT` needs no email.

`resolve` never stages or commits, and never picks a pin nobody verified. Do not
hand-pick a `blob:` line: that records a check against content it may not match.

## Across platforms

Inside git, line endings are handled: pins are git's normalized content, so LF
and CRLF checkouts of the same commit agree, and `init` keeps claim files LF with
`claims/*.md text eol=lf` in `.gitattributes`. A `.gitattributes` rule is still
needed for files git would convert differently from clone to clone — for example
content committed with CRLF bytes, checked out with `core.autocrlf=true` in some
clones — which otherwise reads stale in one clone after verifying in another.
Outside git, pins hash raw bytes, so a line-ending change stales the claim.

## Hooks (installed with the plugin)

| Hook | Who sees it | When |
|---|---|---|
| Session start | Claude | Claims owed to your git `user.email` first (none shown if no email is set); then counts of invalid, conflicted, unpinned, unanchored, stale, missing and owed claims and of dangling markers, with the affected areas |
| After Bash / MCP tool calls | Claude | Only when HEAD moved (commit, merge, rebase, pull, checkout): claims newly owed to you and conflicted claim files first; then now-non-fresh claims backed by files changed anywhere in that commit range, each naming the author email and subject of the newest commit that changed its source; then markers naming no claim |
| End of turn | The user | Problems not present at the last check in this clone — a baseline first taken at session start, then replaced by each end-of-turn check ("since the last check" — pre-existing ones are not repeated), separating drift from uncommitted edits to cited sources from drift that arrived another way (a pull), plus a HEAD-moved report no tool call delivered; a claim the HEAD-moved report already names in the same state is not listed twice |

During an in-progress merge, rebase or cherry-pick, end of turn does not call any
drift "your uncommitted edits". A `git pull` that stops on conflicts does not move
HEAD, so its conflicted claims arrive at end of turn ("became conflicted … run
`claimlock resolve`"), not after the tool call.

Hooks exit 0 always and never set a blocking decision. Each message is capped at
2,000 characters; the HEAD-moved report names at most 10 claims and the
end-of-turn report 5 per category, with `…` when there are more — run
`claimlock check` for the full list. What an end-of-turn report could not show
(past the cap, or past the first five) is reported again at the next end of turn. Hook runs in one session are serialised,
and a run that cannot take the session lock within 5 s is skipped without output.
Hooks are active only when `.claimlock.toml` exists in the opened project
directory or an ancestor — a bare `claims/` directory does not activate them,
and a config in a subdirectory of the opened project is not seen. Everywhere
else they print nothing. Internal errors, and hook input
that was not valid JSON, are recorded in `hook-errors.log` in the plugin data
directory.

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
