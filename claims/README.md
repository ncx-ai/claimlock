# Claims

Every other `*.md` file in this directory is one **claim** about this codebase,
pinned to the content that could falsify it. This file is not a claim —
claimlock skips it by name.

## If you just found these and do not know what they are

Read the claim body, not only its title, and take the frontmatter seriously:

- `sources` lists the files whose change could make the claim false. Each entry
  carries a `blob`: the git blob hash of that file's content at the moment the
  claim was last verified.
- **So if you changed a cited file, the claim may now be false.** Re-check it
  against the code. If it no longer holds, edit it or mark it `refuted` — do
  not leave it asserting something that stopped being true.
- **Never hand-write `blob`, `pins` or `status`.** The tool writes them. `pins`
  is a digest over the whole source set, so an edited pin is detected rather
  than believed — a hand-stamped claim is precisely the lie these pins exist
  to catch.
- A marker written ``Claim: `some-id` `` in prose anywhere in this repository
  points at `some-id.md` here. It means that sentence is covered by a claim
  that something actually checks.

## Getting the tool

claimlock is a Claude Code plugin:

    /plugin marketplace add ncx-ai/claimlock
    /plugin install claimlock@claimlock

That repository may be private. If you cannot reach it, the CLI needs nothing
but Python >= 3.11 from the standard library and runs straight from a clone:

    python3 path/to/claimlock/bin/claimlock check

`check` is the gate (exit 1 when a claim drifted), `affected <paths>` lists the
claims citing a file you are about to touch, and `diff <id>` shows what moved
under a claim since it was verified.

Without any tooling at all you can still do the part that matters: treat a
claim whose cited file you changed as unverified until someone re-checks it.
