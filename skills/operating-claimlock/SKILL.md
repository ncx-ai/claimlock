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

Exit codes: 0 clean, 1 findings, 2 the store could not be read. `check` always
prints `N claims, M sources hashed`. If N is 0 in a repo you believe has
claims, the gate is pointed at the wrong directory — that is a failure of the
gate, not a pass.

In CI, install claimlock from wherever your team actually gets it. If you do not
know the install source, say so and leave a placeholder — do not invent a
package name.

## Import an older store

    claimlock import <old-claims-dir>

Every imported claim arrives **unpinned** and `check` fails until each is
re-checked and verified. That is the point: an import must not launder old
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

## Hooks (installed with the plugin)

| Hook | Who sees it | When |
|---|---|---|
| Session start | Claude | Counts of non-fresh claims and affected areas |
| After Bash / MCP tool calls | Claude | HEAD moved and the commits changed sources of now-non-fresh claims, or added dangling markers |
| End of turn | The user | Problems this session introduced (not pre-existing ones), and commits made outside any tool |

Hooks never block and exit 0 always. In a project without a store they print
nothing. Errors go to `hook-errors.log` in the plugin data directory.

## Red flags

| Thought | Reality |
|---|---|
| "Import then verify everything so CI passes" | That converts unchecked beliefs into pins. Re-check first. |
| "The team already verified these last quarter" | Against content nobody recorded. Their review cannot be pinned to today's files. |
| "I was told to carry the status over" | Report the red gate and the re-check list instead. A green gate built on stamps is worse than a red one. |
| "0 claims, check passed" | Check `claims_dir`. Seeing nothing is not finding nothing. |
| "Disable the hook, it's noisy" | Noise means sources are too broad. Narrow them. |
