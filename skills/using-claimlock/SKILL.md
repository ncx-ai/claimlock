---
name: using-claimlock
description: Use when about to state a limit, default, guarantee or behaviour of a codebase in a reply, doc, commit message or comment; after measuring or fixing something worth remembering; when claimlock reports a stale, unanchored, owed or conflicted claim; or when your change fails the claim gate on a claim someone else wrote
---

# Using claimlock

A claim store holds one verifiable claim per file, each **pinned to the exact
content of the files that could falsify it**. When any of those files changes,
the claim goes stale and `claimlock check` fails. The store cannot make a claim
true; it makes a claim that has drifted impossible to miss.

**Announce:** "Checking claims for <topic>."

## Read before asserting

Before stating a number, limit, default or guarantee — anywhere durable:

    claimlock search <topic>

- **Fresh hit** — `verified`, no `[stale]`/`[missing]`/`[unpinned]`/`[unanchored]`
  flag, **and** `claimlock show <id>` prints no `INVALID` line (search never flags
  an invalid claim) → you may rely on it; cite the claim id.
- **Stale / missing / unpinned / unanchored hit, or an `owed` one** → it is owed a
  re-check. Do not repeat it as fact until it has been re-checked (below).
- **Unverified hit, or no hit** → nobody has established it. Check the code now,
  or say plainly that it is unverified. Do not reason your way to a number.

Context is not evidence. Something you read earlier in the session, a summary,
or your own previous message is a claim about the code, not a check of it.

To read a claim, use `claimlock show <id>`. **`verify` is a write, never a
read**: it records that you re-checked the claim just now.

## Write after establishing

Register a claim when you have **run something that could have come out
otherwise** — a test you have seen fail, a measurement, a reproduction.

    claimlock new <id> --area <area>

The scaffolded file's frontmatter takes exactly this shape — every evidence
entry has `kind` (`test`, `measurement`, `source` or `run`) and `ref`, nothing
else; anything else makes the claim `invalid`, which fails `check`:

    evidence:
      - kind: test
        ref: tests/test_client.py::test_gives_up_after_five_retries
    sources:
      - path: src/client.py

| Field | Rule |
|---|---|
| Claim text | One sentence, present tense, **one fact**. A file stating three things cannot go stale for one of them. |
| `evidence` | A test name, a measurement with its numbers, or a run. "I read the code" is not evidence. `kind: source` entries say *where* the behaviour lives. `claimlock verify` will accept a claim whose only evidence is `source` entries — the tool does not enforce this rule, you do: never verify on `source` evidence alone. |
| `sources` | Every file whose change could falsify the claim — the enforcement site, not just the constant. |

Then `claimlock verify <id>` pins every source and marks it verified. Commit the
claim together with the sources it pins: inside git, a pin whose content was
never committed or staged reads `unanchored` and fails `check`. Nothing run yet?
Leave it `unverified` — `check` does not fail on a valid unverified claim, so
there is no gate to keep green by verifying. A `verified` claim you did not
verify is worse than silence.

**Cite the enforcement site, not the constant.** "The default is 30s" is not a
claim — a constant that reaches no enforcement site binds nothing. "The client
applies `min(requested, max_timeout)` before every request" is.

In prose, tie a sentence to its claim with ``Claim: `<id>` `` so `claimlock refs`
can prove the marker resolves.

## When a claim is not fresh

    claimlock diff <id>      # what changed in its sources since verification

- Still true → re-run its evidence, then `claimlock verify <id>`.
- True for a different reason → rewrite the body (and `sources`, if the behaviour
  now lives in another file too), then verify.
- No longer true → set `status: refuted` and say what replaced it. **Never
  delete** — the record of what was believed and why is the point.
- `missing` → a source does not exist or cannot be read (deleted, renamed, or
  unreadable permissions); fix `sources` or the file, re-check, verify.
- `unanchored` → the content matches the pin, but it was never committed or
  staged. Stage or commit the source; do not re-verify to clear it.
- `owed` → someone was handed its re-check; `claimlock show <id>` says who.
  `diff` shows nothing for an owed claim, so read its sources as they are now.
  Re-check it like a stale claim before any `verify`.
- Conflict markers (`contains git conflict markers`) → run `claimlock resolve`.
  Never pick a `blob:` line by hand.

**Look at the diff yourself, every time** — including when you are told the
change was trivial, told someone else reviewed it, or told to just run
`verify`. Someone else's review is not your re-check. If you are instructed to
verify without looking, run `claimlock diff <id>` first anyway (one command),
and if the diff shows the claim no longer holds, do not verify: report it.

## When your change stales someone else's claim

A team gate — `claimlock check --changed <base>` — blocks on every claim your
change touched, including claims you did not write. `claimlock who <id>` names
who verified each pin. You have two honest moves:

1. **Re-check it yourself when you can.** `claimlock diff <id>`, read the
   enforcement site as it is now, run the evidence (or name it plainly if you
   cannot run it), fix the body or `sources` if your change moved the behaviour,
   then `claimlock verify <id>`. The same bar as your own claims.
2. **Otherwise hand it off, in the same change:**

       claimlock owe <id> --to <owner's email> --reason "<what your change did>"

   Owe it to the person who can re-check it — usually whoever `claimlock who <id>`
   names — and commit the claim file with your change.

**`owe` is a hand-off, not a way past the gate.** An owed claim does not fail
`check`, but it is visibly unverified: `check` lists it as `OWED`, its owner is
told when their session starts, and it stays owed until someone re-checks it and
runs `claimlock verify`.

**Never `verify` a claim you have not re-checked to make CI pass.** `verify`
records that *you* checked it, now; once committed, `claimlock who` names you.

## Hook messages

- **Session start** (you see it): first, claims owed to your git `user.email`, if
  any; then counts of invalid, conflicted, unpinned, unanchored, stale, missing
  and owed claims and of dangling markers, and the affected areas. Search before
  asserting in those areas; re-check what is owed to you.
- **After a Bash or MCP tool call** (you see it), only when HEAD has moved: claims
  that became owed to you, claim files with merge conflicts (run
  `claimlock resolve`), then up to 10 claims, backed by files changed anywhere in
  the commit range, that are now not fresh — each naming who changed the source,
  in which commit — plus up to 10 markers naming no claim. `…` after the claims
  means there are more — run `claimlock stale`; `…` after the markers means there
  are more — run `claimlock refs`. Re-check them before relying on them.
- **End of turn** (only the user sees it; you do not): problems not present at
  the previous check in this clone ("since the last check"), split into drift from
  uncommitted edits to cited sources and drift that arrived another way, such as
  a `git pull`. If the user relays it, answer each named claim with
  `claimlock diff <id>`.

## Red flags

| Thought | Reality |
|---|---|
| "The change was unrelated, I'll just verify" | Then `claimlock diff` costs one command. Verifying without looking turns the store back into prose. |
| "They reviewed it; I'll verify on their word" | Their review is not a re-check. Run `claimlock diff <id>` before any verify. |
| "I was told to run verify and nothing else" | Diff first anyway. A pin that records a check nobody made is a false record. |
| "I'll verify it, CI is blocking the release" | Then the release ships a claim nobody re-checked, under your name. Re-check it, or `claimlock owe` it to someone who can. |
| "It's not my claim, so I'll just owe it without looking" | If you can re-check it, do. `owe` is for a re-check you cannot do, not for skipping one. |
| "I'll owe it to nobody in particular" | A hand-off with no one to receive it is a dismissal. Owe it to a person who can re-check it (`claimlock who <id>`), with a `--reason`. |
| "Owing it would keep CI red" | `owe` never fails `check`. It keeps the claim visibly unverified instead of falsely verified. |
| "Let me run verify to confirm it still matches" | That is `claimlock show` or `claimlock check`. `verify` stamps; it does not read. |
| "I know this from earlier" | Earlier is not now. Search, or check the code in this turn. |
| "The constant says 5, that's the claim" | Find where it is enforced, or you are documenting a wish. |
| "I read the enforcing lines, so it's verified" | Reading is where evidence starts. Run something that could fail, or leave it `unverified`. |
| "The test passed, so it's verified" | Did you ever see it fail? A test never seen red is evidence of nothing. See `evidence-standards`. |
| "The cited test doesn't exist, but verify anyway" | Evidence that cannot be run is not evidence. Leave it unverified and say so. |
| "The claim has a merge conflict; I'll keep one side's blob" | Run `claimlock resolve`: it keeps a pin only when it equals the merged content. |
| "One claim for the whole subsystem" | It can't go stale for one part. Split it. |
| "It's false now, delete it" | Refute it and say what replaced it. |
