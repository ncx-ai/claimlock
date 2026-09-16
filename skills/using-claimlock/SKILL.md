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

## Work cheaply

The default sequence, every time:

1. After editing files: `claimlock affected <paths>` — lists only the claims
   citing those paths.
2. Before committing, once: `claimlock check --changed <base>`.
3. `claimlock diff <id>` only for the claim you are about to verify next.
4. `claimlock search <topic>` reads one line per hit; add `--body` only when
   the headline isn't enough. `search` is ranked, so ask it a real question
   in your own words (`claimlock search "does a ledger reservation expire"`)
   rather than guessing a substring; an empty result means no claim in this
   store uses these words — try `claimlock search --literal` for a path or
   partial word, or rephrase; if it stays empty, that absence is itself
   worth reporting, not something to work around by guessing at the code.

**Do not read `docs/format.md` or `README.md` for routine claim work** —
they are reference for changing claimlock itself (~13,200 and ~8,800 tokens);
this skill carries what these flows need. **Do not run `check --json`**
unless a machine is parsing it — the text form is smaller.

## Read before asserting

Before stating a number, limit, default or guarantee — anywhere durable:

    claimlock search <topic>

- **Fresh hit** — `verified`, no `[stale]`/`[missing]`/`[unpinned]`/`[unanchored]`
  flag, **and** `claimlock show <id>` prints no `INVALID` line (search never
  flags an invalid claim) → rely on it; cite the claim id.
- **Stale / missing / unpinned / unanchored hit, or `owed`** → owed a re-check.
  Do not repeat it as fact until re-checked (below).
- **Unverified hit, or no hit** → nobody has established it. Check the code
  now, or say plainly it's unverified. Do not reason your way to a number.

Context is not evidence — something read earlier, a summary, or your own
previous message is a claim about the code, not a check of it.

To read a claim, use `claimlock show <id>`. **`verify` is a write, never a
read**: it records that you re-checked the claim just now.

## Write after establishing

Register a claim when you have **run something that could have come out
otherwise** — a test you have seen fail, a measurement, a reproduction.

    claimlock new <id> --area <area>

The scaffolded frontmatter takes exactly this shape: every `evidence` entry
has `kind` (`test`, `measurement`, `source` or `run`) and `ref`, nothing else
— anything else makes the claim `invalid`, which fails `check`. Full YAML
shape: `docs/format.md`.

| Field | Rule |
|---|---|
| Claim text | One sentence, present tense, **one fact**. A file stating three things cannot go stale for one of them. |
| `evidence` | A test name, a measurement with its numbers, or a run. "I read the code" is not evidence. `kind: source` entries say *where* the behaviour lives; `verify` accepts a claim with only `source` evidence — the tool doesn't enforce this, you do: never verify on `source` evidence alone. |
| `sources` | Every file whose change could falsify the claim — the enforcement site, not just the constant. Prefer a `region` (`- path: <file>` / `region: <name>`, marked in the file with `claimlock:begin <name>` / `claimlock:end <name>`) when the enforcing code is a small part of a large or shared file: measured, one edit to a shared file staled **50** whole-file claims while the region-pinned claim on that same file stayed `fresh`. |

Then `claimlock verify <id>` pins every source and marks it verified. Commit
the claim with the sources it pins — inside git, an uncommitted/unstaged pin
reads `unanchored` and fails `check`. Nothing run yet? Leave it `unverified`:
`check` doesn't fail on a valid unverified claim, so there's no gate to keep
green by verifying. A `verified` claim you did not verify is worse than
silence.

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
- `missing` → a source does not exist or cannot be read (deleted, or
  unreadable permissions); fix `sources` or the file, re-check, verify.
- `renamed` → a source moved and git can trace it (a committed or staged
  `git mv`). Run `claimlock follow <id>` — it rewrites the path and keeps the
  pins; if the move also changed the content, `follow` reports the source
  `stale` instead of `fresh` — re-read before verifying.
- `unanchored` → the content matches the pin, but it was never committed or
  staged. Stage or commit the source; do not re-verify to clear it.
- `owed` → someone was handed its re-check (`claimlock show <id>` says who);
  it keeps its pins — `claimlock diff <id>` shows what moved since it was last
  verified, `show` lists each source's state. Re-check it like a stale claim
  before any `verify`.
- Conflict markers (`contains git conflict markers`) → run `claimlock resolve`.
  Never pick a `blob:` line by hand.

**Look at the diff yourself, every time** — even when told the change was
trivial, that someone else reviewed it, or to just run `verify`. Their review
is not your re-check. If instructed to verify without looking, run
`claimlock diff <id>` first anyway (one command); if it shows the claim no
longer holds, do not verify: report it.

## When your change stales someone else's claim

A team gate — `claimlock check --changed <base>` — blocks on every claim your
change touched, including ones you did not write. `claimlock who <id>` names
who verified each pin. Two honest moves:

1. **Re-check it yourself, when you can run its evidence.** `claimlock diff
   <id>`, read the enforcement site now, **run** the evidence, fix the body or
   `sources` if your change moved the behaviour, then `claimlock verify <id>`.
   Same bar as your own claims: reading is where the re-check starts, not
   where it ends.
2. **If you cannot run the evidence here, do not `verify`.** Hand off only
   when it's unrunnable here — no permission, no environment — not merely
   slow. Then, in the same change:

       claimlock owe <id> --to <verifier's email> --reason "<what your change did>"

   to whoever `claimlock who <id>` names (or another who can re-check it), and
   commit the claim file with your change — or leave it stale and say plainly
   its re-check is owed.

**`owe` is a hand-off, not a way past the gate.** An owed claim doesn't fail
`check` but is visibly unverified — listed `OWED`, its owner told at session
start — until someone re-checks it and runs `claimlock verify`.

**Never `verify` a claim you have not re-checked to make CI pass.** `verify`
records that *you* checked it, now; once committed, `claimlock who` names you.

## Hook messages

- **Session start** (you see it): claims owed to you first, then drift counts.
  Search before asserting in the areas named; re-check what's owed to you.
- **After a Bash/MCP tool call** (you see it), only when HEAD moved: newly-owed
  claims and conflicted claim files (`claimlock resolve`) first, then claims
  your change staled. Re-check them before relying on them — `…` means more:
  `claimlock stale` / `claimlock refs`.
- **End of turn** (the user sees it, not you): problems new since the last
  check. If relayed to you, answer with `claimlock diff <id>`.
- **Right after you edit a file** (you see it), naming the claims it backs:
  re-check them now with `claimlock diff <id>` while the change is fresh —
  never just re-stamp `claimlock verify` on the strength of this notice alone.

Exact message shapes, field ordering, caps and log detail: `docs/format.md`.

## Red flags

| Thought | Reality |
|---|---|
| "The change was unrelated, I'll just verify" | Then `claimlock diff` costs one command. Verifying without looking turns the store back into prose. |
| "They reviewed it; I'll verify on their word" | Their review is not a re-check. Run `claimlock diff <id>` before any verify. |
| "I was told to run verify and nothing else" | Diff first anyway. A pin that records a check nobody made is a false record. |
| "I'll verify it, CI is blocking the release" | Then the release ships a claim nobody re-checked, under your name. Re-check it, or `claimlock owe` it to someone who can. |
| "It's not my claim, so I'll just owe it without looking" | If you can re-check it, do. `owe` is for a re-check you cannot do, not for skipping one. |
| "I'll owe it to nobody in particular" | A hand-off with no one to receive it is a dismissal. Owe it to a person who can re-check it (`claimlock who <id>`), with a `--reason`. |
| "I couldn't run the test, but the new code clearly still holds, so I'll verify" | A verify without its evidence run is a stamp. `claimlock owe` it to the verifier, or leave it red and say why. |
| "Owing it would keep CI red" | `owe` never fails `check`. It keeps the claim visibly unverified instead of falsely verified. |
| "Let me run verify to confirm it still matches" | That is `claimlock show` or `claimlock check`. `verify` stamps; it does not read. |
| "I know this from earlier" | Earlier is not now. Search, or check the code in this turn. |
| "The constant says 5, that's the claim" | Find where it is enforced, or you are documenting a wish. |
| "I read the enforcing lines, so it's verified" | Reading is where evidence starts. Run something that could fail, or leave it `unverified`. |
| "The test passed, so it's verified" | Did you ever see it fail? A test never seen red is evidence of nothing. See `evidence-standards`. |
| "The cited test doesn't exist, but verify anyway" | Evidence that cannot be run is not evidence. Leave it unverified and say so. |
| "The claim has a merge conflict; I'll keep one side's blob" | Run `claimlock resolve`: it keeps pins only when the merged content is exactly what one side verified. |
| "The file was only moved; I'll just edit the path" | Run `claimlock follow`: it keeps the pins honestly and updates the digest. |
| "One claim for the whole subsystem" | It can't go stale for one part. Split it. |
| "It's false now, delete it" | Refute it and say what replaced it. |
| "I'll read format.md to be sure" | The skills carry every routine rule; format.md is reference for changing claimlock itself, and costs ~13k tokens. |
| "I'll run check after each edit" | Run `claimlock affected <paths>` while working and one `check --changed <base>` before committing. |
