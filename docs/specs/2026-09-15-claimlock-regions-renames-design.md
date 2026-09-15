# claimlock — region pins and rename following

Status: approved 2026-09-15 (design choices made with the user: marker-comment
regions; renames reported with a fix command). Base specs:
`2026-09-14-claimlock-design.md`, `2026-09-14-claimlock-teams-design.md`
(amendments T1–T15 still hold unless stated here).

## 1. Problems

1. **File-level pins stale too much.** Any edit anywhere in a cited file stales
   every claim citing it. In a large shared file that is constant noise, and
   noise trains people to re-verify without re-checking — the one habit
   claimlock exists to stop.
2. **A renamed source reads `missing` with no hint.** A `git mv` breaks every
   claim citing the file, and nothing says where it went. Fixing it by hand
   also breaks the `pins:` digest, forcing a full re-verify of a pure move.

## 2. Region pins

### 2.1 Markers in the source

A line containing `claimlock:begin <name>` opens a region; a line containing
`claimlock:end <name>` closes it. The markers may sit inside any comment
syntax (`#`, `//`, `<!-- -->`, …): a line matches when it contains
`claimlock:begin` or `claimlock:end`, then whitespace, then the name, and the
name is not followed by another `[a-z0-9-]` character. Names match
`^[a-z0-9][a-z0-9-]*$`.

The region is the lines **strictly between** the two marker lines, so
restyling the marker comments never changes a pin. Regions of different names
may nest or overlap. Extraction of one name fails, with a reason, when:

- no `begin` line for it — `region '<name>' not found`
- a second `begin` for it — `region '<name>' begins more than once`
- no `end` after its `begin` — `region '<name>' has no end marker`
- an `end` before any `begin` — `region '<name>' ends before it begins`
- a second `end` for it, with no matching second `begin` — `region '<name>' ends more than once`
- the file is not valid UTF-8 — `not UTF-8, so regions cannot be read`

A failed extraction reads the source `missing` (with the reason in `diff` and
`show`); `verify` refuses with the reason.

### 2.2 Region text and hash

The file's bytes are decoded as UTF-8; lines are split at `\n` only; a trailing
`\r` is removed from each line. The region text is its lines each followed by
`\n` (an empty region is the empty string). The region **hash** is
`sha1(b"blob <len>\0" + text.encode("utf-8"))` — the same blob-hash function
as whole-file pins, applied to the region text. Git clean filters do not apply
to region text; only this line-ending normalization does.

### 2.3 In the claim

A source map entry gains two keys:

```yaml
sources:
  - path: src/retry.py
    region: retry-cap
    blob: <git blob of the whole file when verified>
    hash: <region hash when verified>
```

Rules (`problems`):

- `region` must match the name pattern — `source '<p>' has a malformed region name`.
- `hash` must be 40 lowercase hex — `source '<p>' has a malformed hash`.
- `hash` without `region` — `source '<p>' has a hash but no region`.
- A region entry with exactly one of `blob`/`hash` — `source '<p>#<r>' must pin both blob and hash`.
- Duplicates are judged by the pair (path, region): the same path may be listed
  once whole and once per region. `source '<p>#<r>' is listed twice`.
- Unknown keys are now anything outside `path, blob, region, hash`.

A source's **key** is `path` for a whole-file source and `path#region` for a
region source; every per-source listing (`check`, `stale`, `show`, `diff`,
`--json`, hooks) names sources by key.

### 2.4 Freshness of a region source

Worst-source-wins as before. For a region source:

- file missing, or extraction fails → `missing` (or `renamed`, §3)
- no `hash` → `unpinned`
- current region hash ≠ `hash` → `stale`
- otherwise anchoring (inside git): anchored when `blob` is in the anchor set
  or the path is ignored (as for whole files), **or** when the file's staged
  version (index stage 0), read from git, contains the region with the same
  hash. The fallback runs only for a region pin whose blob is not anchored, so
  the common path adds no git call. Without it, verifying with uncommitted
  edits elsewhere in the file would leave the pin unanchored forever.
- else `fresh`

Region hashes use the stat cache: an entry keyed `"<path>\0<region>"` holds
`[size, mtime_ns, hash, tag]` with the same racy guard and tag as file entries.
`verify` and `resolve` bypass the cache.

### 2.5 Digest

`pin_digest` entries: a whole-file source stays `[path, blob or ""]` (existing
digests stay valid); a region source is `[path, region, blob or "", hash or ""]`.
Sorted as before.

### 2.6 Commands

- `verify` computes, for each region source, the whole file's blob (cache
  bypassed) and the region hash (from the file read directly), refusing on an
  extraction failure: `<id>: source <p>#<r>: <reason>`. Output lines are
  `  <key> @ <first 12 of pin>`, the pin being `hash` for a region.
- `diff` for a stale region: reads the pinned `blob` from git, extracts the
  region from it (failure: `--- <key>: the region cannot be found in the pinned
  content (<reason>)`), extracts it from the current file, and prints a unified
  diff of the two regions with headers `<key> @ <hash12> (verified)` and
  `<key> (now)`. A missing region prints `--- <key>: <reason>`.
- `show` lists `<key> — <state> (<pin12>)`, the pin being `hash` for a region.
- `who` attributes a region pin by its `    hash: <sha>` line (unique per
  region), a whole-file pin by its `    blob:` line as before.
  `gitio.verifier(root, claim_rel, field, value)` takes the field name.
- `resolve` compares by key: both sides must list the same keys; a side is
  whole when every key's current pin (region hash for a region, blob for a
  whole file) equals that side's pin; the owed path picks per key. A region
  entry's `blob` travels with its `hash` from the same side.
- `check --changed` scope, `affected`, and hook attribution stay per path.

## 3. Renames

### 3.1 Detection (git only)

For each cited path whose file does not exist (only these — the common path
runs no extra git), in the root:

1. `git log -1 --format=%H --diff-filter=D -- <path>` names the commit `C`
   that last deleted it. If there is one, `git diff -M --name-status -z
   --diff-filter=R C^` (C's parent against the working tree, all tracked
   paths) is searched for a rename whose old path is `<path>`; the reported
   commit is `C` (7 chars). A chain of renames (a → b → c) is reported end to
   end, because the comparison is from before the first deletion to now.
2. If no commit deleted it, `git diff -M --name-status -z --diff-filter=R
   --cached HEAD` is searched (a staged `git mv`); the reported commit is
   `uncommitted`.
3. A rename counts only if the new path exists now and is inside the root.

A plain `mv` that is neither committed nor staged is invisible to git (probe,
2026-09-15: it shows as a deletion plus an untracked file) and still reads
`missing`; the hint says to stage the move. Detection uses git's default
similarity threshold (50%), so a rename with edits is found and then reads
`stale` after `follow`. All git calls live in `gitio.find_renames(root, rels)
-> {old: (new, sha7 | "uncommitted")}` and degrade to `{}`.

### 3.2 The `renamed` state

A source whose file is missing and which `find_renames` maps to a new path is
`renamed` instead of `missing`. Severity: `missing` > `renamed` > `stale` >
`unanchored` > `unpinned` > `fresh`. `renamed` is non-fresh: it fails `check`
for a verified claim and is listed for owed claims like the other states.
`NON_FRESH` becomes `("unpinned", "unanchored", "stale", "missing",
"renamed")` — appended last, so existing summary text keeps its order.

Hint (`check`, `show`): `a source was renamed — run: claimlock follow {id}`.
Per-source lines in `check` read `<key>: renamed → <new> (<sha7 | uncommitted>)`.
`--json` source entries gain `"renamed_to": "<new>"` for a renamed source.
`diff` prints `--- <key>: renamed to <new> in <sha7 | uncommitted> — run:
claimlock follow <id>`.

### 3.3 `claimlock follow <id>...`

For each claim: rewrites the `path` of every renamed source to its new path,
keeping `region`, `blob` and `hash`. If the claim had a `pins:` digest that
matched its old sources, the digest is recomputed over the new sources; a
mismatched or absent digest stays as it was. Status and every other field are
unchanged. Then the claim is re-evaluated and each followed source prints:

`followed <id>: <old> → <new> (<new state>)` — e.g. `fresh` for an unchanged
move, `stale` when the content changed in the rename.

Refusals (exit 1, file untouched): no such claim; conflicted or unparseable
claim; outside git (`follow needs a git repository`); no renamed sources
(`<id>: no renamed sources`); a new path the claim already cites with the same
region (`<id>: <new> is already cited`). Works for any status.

## 4. Self-test

Plain arm gains a region probe: a region pin stays `fresh` after an edit
outside the region, reads `stale` after an edit inside it, and `missing` after
its end marker is removed. The git arm gains a rename probe: a committed
`git mv` of a pinned source reads `renamed`, and after `follow` reads `fresh`.

## 5. Non-goals

Pattern-based or language-aware regions; line ranges; following unstaged plain
moves; automatic following without an edit to the claim; regions in files git
converts with clean filters.

## 6. Risks

- **Marker drift:** a refactor that moves code out from between markers leaves
  a fresh region that no longer holds the enforcing code. Mitigated only by
  review; documented in README Limits.
- **Rename false positives:** git's similarity heuristic can pair a deleted
  file with an unrelated new one. `follow` is explicit and its result shows the
  new state; a wrong pairing reads `stale` rather than `fresh` unless the
  content is identical.
- **Detection cost** on very large repositories: only for missing sources, one
  `log` plus one tree diff per distinct deleting commit.
