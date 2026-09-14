---
name: operating-claimlock
description: Use when adding claimlock to a repository, wiring its gate into CI or a pre-commit hook, importing an existing claim directory, triaging many stale claims at once, or interpreting claimlock hook output and noise
---

# Operating claimlock

## Adopt

    claimlock init            # .claimlock.toml, claims/, .gitignore entry
    claimlock self-test       # prove the detectors fire on this machine

Works in a plain directory; git is optional. Pins are git blob hashes computed
without git, so a store created before `git init` stays valid after it.

`.claimlock.toml` keys (all optional): `claims_dir`, `marker_globs`,
`marker_pattern`. Unknown keys are an error — a typo must not silently fall
back to a default.

## Gate

    claimlock self-test && claimlock check && claimlock refs

Exit codes: 0 clean, 1 findings, 2 the store could not be read (no claims
directory, or a bad `.claimlock.toml`) — exit 2 prints only an error. When the
store is readable, plain `check` ends with a summary line
`claimlock: N claims, M sources hashed — …`, and `check --json` carries the same
numbers as `claims` and `sources_hashed`. If N is 0 in a repo you believe has
claims, the gate is pointed at the wrong directory — that is a failure of the
gate, not a pass.

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

## Triage many stale claims

    claimlock stale                 # id, area, state, changed paths
    claimlock affected <path>...    # which claims a file backs
    claimlock diff <id>

Group by changed path: one refactor usually stales a cluster. Re-check each
claim against its enforcement site; verify only the ones you re-checked. A
large shared file stales every claim citing it — prefer the narrowest file
that actually enforces the behaviour when writing `sources`.

## Pin conflicts on merge

Two branches that verified the same claim conflict on its `blob` lines. Take
either side, re-check the claim, then `claimlock verify <id>`.

## Across platforms

Pins hash working-tree bytes. A clone that checks files out with CRLF
(`core.autocrlf=true`, a Windows runner) sees every claim pinned on an LF clone
as stale, and verifying there stales it for the LF clones. In a repository used
across platforms, commit `* text=auto eol=lf` to `.gitattributes` (or set
`core.autocrlf=false`) before verifying claims.

## Hooks (installed with the plugin)

| Hook | Who sees it | When |
|---|---|---|
| Session start | Claude | Counts of invalid, unpinned, stale and missing claims and of dangling markers, with the affected areas |
| After Bash / MCP tool calls | Claude | Only when HEAD moved (commit, merge, rebase, pull, checkout): now-non-fresh claims backed by files changed anywhere in that commit range, and markers naming no claim |
| End of turn | The user | Problems not present at the previous end-of-turn check in this clone ("since the last check" — including drift that arrived by pull, not only this session's edits; pre-existing ones are not repeated), plus a HEAD-moved report no tool call delivered; a claim the HEAD-moved report already names is not listed twice |

Hooks exit 0 always and never set a blocking decision. Each message is capped at
2,000 characters; the HEAD-moved report names at most 10 claims and the
end-of-turn report 5 per category, with `…` when there are more — run
`claimlock check` for the full list. Hook runs in one session are serialised,
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
| "0 claims, check passed" | Check `claims_dir`. Seeing nothing is not finding nothing. |
| "Disable the hook, it's noisy" | Noise means sources are too broad. Narrow them. |
