# Claim format reference

This is the normative reference, derived from the implementation
(`lib/claimlock/claims.py`, `lib/claimlock/frontmatter.py`,
`lib/claimlock/pins.py`, `lib/claimlock/regions.py`, `lib/claimlock/gitio.py`,
`lib/claimlock/ops.py`, `lib/claimlock/merge.py`, `lib/claimlock/cli.py`,
`lib/claimlock/project.py`).
If this document and the code ever disagree, the code is right and this file
has drifted — file an issue.

A claim is one Markdown file in the claims directory (`claims/` by default),
named `<id>.md`, holding a strict-YAML-subset frontmatter block followed by
free text. `claims_dir/README.md` is always ignored by the loader and by
`import`, so you can put an index page in the claims directory without it
being read as a claim.

## Frontmatter: the accepted YAML subset

The parser (`frontmatter.py`) is a **strict subset** of YAML, not a full
implementation — it recognizes exactly the shapes below and raises an error
naming the file and line on anything else, deliberately, so a shape it cannot
represent is never silently misread.

A claim file must start with a `---` line (line 1) and have a later `---` line
closing the frontmatter block; everything after the closing `---` is the claim
body.

**Line endings.** A claim file with CRLF line endings is accepted and read
exactly as its LF form (every `\r\n` becomes `\n` before anything else looks at
the text). A lone `\r` that is not part of a `\r\n` pair is still a parse error.
`claimlock init` appends `claims/*.md text eol=lf` to `.gitattributes` (never
twice) so claim files stay LF in every clone; the rule names the default claims
directory, so a store configured with another `claims_dir` needs its own rule.

Every value is one of:

**A scalar**, on the same line as its key:

```yaml
id: retries-are-capped
```

**An explicit empty list**:

```yaml
sources: []
```

**A list**, introduced by a bare `key:` with items on following lines,
indented two spaces with a leading `- `:

```yaml
evidence:
  - kind: test
    ref: tests/test_client.py::test_gives_up_after_five_retries
```

A list item is either a plain scalar (`  - some-value`) or a **flat map**: the
first `key: value` sits on the `- ` line itself, and further keys continue at
4-space indentation on their own lines (no nested lists, no nesting deeper
than one level):

```yaml
sources:
  - path: src/client.py
    blob: 4b825dc642cb6eb9a060e54bf8d69288fbee4904
```

A bare `key:` with nothing after it and no following indented `- ` line means
`None` (i.e. the key is absent for validation purposes).

**Scalars** are one of:
- **Plain**: everything up to an unescaped `#` (a `#` only starts a comment at
  the start of the value or after whitespace — `foo#bar` is the literal string
  `foo#bar`). Leading/trailing whitespace is stripped.
- **`'single-quoted'`**: `''` is an escaped literal `'`; nothing else is
  escaped.
- **`"double-quoted"`**: standard JSON string escapes.

All values are strings — there is no numeric, boolean or date inference. A
plain scalar starting with one of `[{&*!|>%@\`` is rejected
(`unsupported YAML syntax starting with '<char>'; quote the value`) rather
than guessed at, because those characters mean something in real YAML and
silently treating them as literal text would misread a file that looks valid.
(That is why `claimlock owe` writes `owed_by: "amy@example.com"` quoted.)

Blank lines and full-line comments (`#...` after stripping) are always
allowed between keys and inside list blocks.

## Fields

| Field | Type | Rule |
|---|---|---|
| `id` | string | Required. Must match `^[a-z0-9][a-z0-9-]*$` (kebab-case) and equal the filename stem (`retries-are-capped.md` → `id: retries-are-capped`). Missing `id` → `missing 'id'`. |
| `area` | string | Optional; defaults to `"unfiled"` if absent. Must be a single scalar, not a list. |
| `status` | string | One of `verified`, `unverified`, `refuted`, `owed`. Defaults to `"unverified"` if absent. |
| `owed_by` | string | Only with `status: owed`, where it is required: an email address (`^[^@\s]+@[^@\s]+$`). |
| `owed_since` | string | Only with `status: owed`, where it is required: a commit id (7–40 lowercase hex) or `none`. `claimlock owe` writes HEAD's 7-character id, or `none` outside git. |
| `verified_at` | string | Accepted for older claims and **ignored**; must be a single scalar if present. `claimlock verify` removes it. Who verified a pin and when is read from git (`claimlock who`). |
| `evidence` | list | A list of `{kind, ref}` maps. `kind` must be one of `test`, `measurement`, `source`, `run`. No other keys are allowed on an evidence entry. |
| `sources` | list | A list of source entries — see below. |
| `pins` | string | Optional. The digest of the whole pin set, written by `claimlock verify` directly after the `sources` block: sha1 of the JSON array of the sorted entries below (an unpinned source's blob/hash is `""`). Must be 40 lowercase hex and equal that digest of the listed sources, else the claim is invalid; reordering sources keeps it. A claim with no `pins` line (verified before it existed) is valid. Every `verify` rewrites this one line, so two branches that re-verify one claim to different contents conflict on it even when they changed different sources. |

Any frontmatter key outside this set is `unknown field '<k>' (allowed: id, area, status, verified_at, owed_by, owed_since, evidence, sources, pins)`.

An `owed` claim keeps its `sources` pins exactly as they were; `claimlock
verify` sets `status: verified`, re-pins every source and removes `owed_by`,
`owed_since` and `verified_at`.

### `sources` entries

A source entry is either a bare path string, or a map:

```yaml
sources:
  - src/legacy.py                # no pin yet — "unpinned"
  - path: src/client.py
    blob: 4b825dc642cb6eb9a060e54bf8d69288fbee4904
  - path: src/retry.py
    region: retry-cap
    blob: 4b825dc642cb6eb9a060e54bf8d69288fbee4904
    hash: da39a3ee5e6b4b0d3255bfef95601890afd80709
```

- `path` is required and must be a project-root-relative POSIX path that
  stays inside the project root: no leading `/`, no `\`, no Windows drive
  letter, no `..` segment. A path that fails this is
  `source path '<p>' must be relative and stay inside the project root`.
- `blob`, if present, must be 40 lowercase hex characters, or
  `source '<p>' has a malformed blob (expected 40 lowercase hex)`. For a
  region source, `blob` is still the pin of the **whole file** (used for
  anchoring and `diff`), never the region: `verify` writes the blob of the
  staged file (index stage 0) when it holds the same region with the same
  hash, else of the working tree.
- `region`, if present, must match `^[a-z0-9][a-z0-9-]*$` (see "Regions"
  below), or `source '<p>' has a malformed region name`.
- `hash`, if present, must be 40 lowercase hex characters, or
  `source '<p>' has a malformed hash`. `hash` is the pin the region's
  content is judged against.
- `hash` without `region` is `source '<p>' has a hash but no region` — `hash`
  only makes sense on a region entry.
- A region entry (one with a valid `region`) must pin both `blob` and `hash`
  together or neither: exactly one present is
  `source '<p>#<r>' must pin both blob and hash`.
- Any other key on a source map — anything outside `path`, `blob`, `region`,
  `hash` — is `source '<p>' has unknown keys [...]`.
- Duplicates are judged by the pair `(path, region)`: the same `path` may be
  listed once whole and once per region, but not twice with the same
  `region` (or twice whole). `source '<p>#<r>' is listed twice`, or
  `source '<p>' is listed twice` for two whole-file entries. Judged by the
  raw `region` value as written, even a malformed one — so a malformed
  region name never falls back to colliding with a genuine whole-file entry
  for the same path, while two identical malformed entries still collide
  with each other.
- A source entry with no usable `path` at all is
  `source entry needs a 'path': <repr>`.

A source's **key** — how every per-source listing (`check`, `stale`, `show`,
`diff`, `--json`, hooks) names it — is `path` for a whole-file source, or
`path#region` for a region source (`src/retry.py#retry-cap` above). The
**pin** a source is judged against is `hash` for a region source, `blob` for
a whole-file one.

`verify` and `resolve` write each pin as a map whose `blob:`/`hash:` line is
exactly four spaces, the field name, `: `, and the 40-hex id — the line
`claimlock who` searches history for; a region entry's lines are written in
the order `path`, `region`, `blob`, `hash`.

The `pins:` digest (see the Fields table above) is computed over one entry
per source, sorted: `[path, blob or ""]` for a whole-file source (unchanged
from before regions existed, so every digest ever written stays valid), or
`[path, region, blob or "", hash or ""]` for a region source.

### Regions

A `region` source pins the text of a named, marker-delimited region of the
file instead of the whole file (`lib/claimlock/regions.py`). A line
containing `claimlock:begin <name>` opens the region named `<name>`; a line
containing `claimlock:end <name>` closes it. The markers may sit inside any
comment syntax — a line matches when it contains `claimlock:begin` or
`claimlock:end`, then whitespace, then the name, and the name is not
followed by another `[a-z0-9-]` character (so `claimlock:begin r1-extra`
never opens region `r1`). Names match `^[a-z0-9][a-z0-9-]*$`. Only the
first marker on a line is recognised: a second marker on the same line is
ignored, so such a layout typically fails loudly (`not found`, `has no end
marker`, …) rather than opening or closing a region.

The region is the lines **strictly between** the two marker lines — the
marker lines themselves are never part of it, so restyling the marker
comments never changes the pin. Regions of different names may nest or
overlap. Extracting one name from a file fails, with a reason, when:

- there is no `begin` line for it — `region '<name>' not found`
- there is a second `begin` for it — `region '<name>' begins more than once`
- there is no `end` after its `begin` — `region '<name>' has no end marker`
- an `end` for it appears before any `begin` — `region '<name>' ends before it begins`
- there is a second `end` for it, with no matching second `begin` —
  `region '<name>' ends more than once`
- the file is not valid UTF-8 — `not UTF-8, so regions cannot be read`

A failed extraction reads the source `missing` (or `renamed`, if the file
itself is also missing and traced to a new path — see "Renames" below); `diff`
and `show` print the reason, and `verify` refuses with it
(`<id>: source <p>#<r>: <reason>`).

The file's bytes are decoded as UTF-8; lines are split at `\n` only, and a
trailing `\r` is removed from each (an empty region is the empty string). The
region **hash** — the value written as `hash` — is `sha1("blob <len>\0" +
text)`, the region's lines each followed by `\n`, using the same formula as a
whole-file `blob` but applied to the region text rather than the file's raw
bytes. Git clean filters never apply to region text, only this line-ending
normalization does.

Region hashes use the same stat cache as whole-file blobs, keyed
`"<path>\0<region>"`; `verify` and `resolve` bypass it, same as for whole
files.

### `evidence` entries

```yaml
evidence:
  - kind: run
    ref: claimlock self-test
```

- Needs both `kind` (one of `test`, `measurement`, `source`, `run`) and a
  non-empty `ref`, or `evidence entry needs 'kind' and 'ref': <repr>`.
- An unrecognized `kind` is `evidence kind '<k>' is not one of test, measurement, source, run`.
- Any key besides `kind`/`ref` is `evidence entry has unknown keys [...]`.

### The body

Everything after the closing `---`, stripped of leading/trailing blank lines.
Empty body → `no claim text after the frontmatter`. `claimlock owe --reason
<text>` appends one line to it: `Owed <YYYY-MM-DD> by <git user.email, or
"unknown">: <text>`.

## Conflicted claims

A claim file is **conflicted** when it contains both a line starting with
`<<<<<<< ` (seven `<` and a space) and a line starting with `>>>>>>> ` (seven
`>` and a space), anywhere in the file. The check runs after CRLF
normalization and before frontmatter parsing, so a conflicted claim carries no
parse error, no metadata and no freshness. Its only problem is
``<file>: contains git conflict markers — run `claimlock resolve` ``.

Because the whole file is scanned, a body that quotes both marker lines (inside
a code fence, for example) also reads as conflicted.

`claimlock verify` and `claimlock owe` refuse a conflicted claim; `claimlock
resolve` is the command for it (see below).

## Every message `problems()` can emit

`claims.py::problems` returns every reason a claim cannot be trusted as
written; it does not stop at the first one. The complete set of message
*prefixes* (some interpolate the offending value):

- A conflicted file short-circuits everything else and is the claim's only
  problem: ``<file>: contains git conflict markers — run `claimlock resolve` ``.
- A parse error short-circuits everything else and is the claim's only
  problem: `<file>:<line>: <message>` — see "Parse errors" below.
- `unknown field '<k>' (allowed: id, area, status, verified_at, owed_by, owed_since, evidence, sources, pins)`
- `missing 'id'`
- `id '<id>' is not kebab-case ([a-z0-9][a-z0-9-]*)`
- `id '<id>' does not match filename '<file>'`
- `'<area|verified_at|owed_by|owed_since>' must be a single value`
- `status '<s>' is not one of verified, unverified, refuted, owed`
- `no claim text after the frontmatter`
- `'evidence' must be a list`
- `evidence entry needs 'kind' and 'ref': <repr>`
- `evidence kind '<k>' is not one of test, measurement, source, run`
- `evidence entry has unknown keys [...]`
- `'sources' must be a list`
- `source entry needs a 'path': <repr>`
- `source '<p>' has unknown keys [...]`
- `source '<p>' has a malformed blob (expected 40 lowercase hex)`
- `source '<p>' has a malformed region name`
- `source '<p>' has a malformed hash`
- `source '<p>' has a hash but no region`
- `source '<p>#<r>' must pin both blob and hash`
- `source path '<p>' must be relative and stay inside the project root`
- `source '<p>' is listed twice` (or `source '<p>#<r>' is listed twice` for a
  region source — duplicates are judged by `(path, region)`)
- `'pins' must be a pin-set digest (40 lowercase hex), as written by claimlock verify`
- `pins digest does not match the listed sources — they were edited by hand or combined from different verifications; re-check the claim, then claimlock verify`
  (`claimlock verify` does not raise this one: it rewrites the digest)
- `status is 'owed' but 'owed_by' is not an email address`
- `status is 'owed' but 'owed_since' is not a commit id (7-40 hex) or 'none'`
- `status is 'owed' but no sources are listed`
- `'<owed_by|owed_since>' is only valid with status: owed`
- `status is 'verified' but no evidence is cited`
- `status is 'verified' but no sources are listed, so it can never go stale`

The last two only fire when checking (or attempting to set) `status:
verified` — `claimlock verify` runs `problems()` with `as_status="verified"`
so a claim with no evidence or sources yet is refused verification even while
it is still nominally `unverified` or `owed` on disk. In that mode the
"only valid with status: owed" check is skipped, because `verify` removes
those fields.

### Parse errors (frontmatter is unreadable at all)

A parse error makes the whole claim `invalid`; no other check runs, because a
file whose shape could not be understood cannot be trusted to report its own
other problems correctly. Reported as `<file>:<line>: <message>`:

- `file must start with a '---' line` (line 1)
- `no closing '---' line` (line 1)
- `CR line endings are not supported; convert to LF` (line 1 — a lone `\r`
  left after CRLF normalization; the file is read as raw bytes specifically so
  a `\r` is never silently translated away first)
- `not valid UTF-8` (line 1 — the file could not be decoded at all)
- `cannot be read: <reason>` (line 1 — the claim file itself could not be
  opened, e.g. permissions; reported as an invalid claim, never raised)
- `expected 'key: value' at column 0`
- `unexpected indentation; expected 'key: value' at column 0`
- `duplicate key '<k>'`
- `bad double-quoted string: <json error>` (the quoted text isn't valid JSON)
- `bad double-quoted string` (valid JSON, but not a string — defensive; a
  leading `"` currently always parses to a JSON string when it parses at all)
- `text after closing quote`
- `bad single-quoted string`
- `unsupported YAML syntax starting with '<char>'; quote the value`
- `unexpected indentation; list items are '  - ' and map continuations are 4 spaces`

A claims directory that exists but cannot be listed is not a claim problem:
every command that reads the store exits 2 (`claims directory <dir> cannot be
read: <reason>`), because reading it as empty would be a false clean.

## Freshness: the six states

Freshness is evaluated only for a claim whose `status` is `verified`, that is
not conflicted, and whose frontmatter parses cleanly; every other claim's
freshness is `None`. An `unverified`, `refuted` or `owed` claim, or an invalid
one, is neither fresh nor stale — it simply isn't being compared with its
sources.

For each source of a verified claim (a source whose path fails the
root-relative rule is skipped here; `problems()` reports it) — a region
source is judged by its **region hash** against `hash`; every other source is
judged by its **whole-file pin** against `blob`:

| State | Meaning |
|---|---|
| `fresh` | The file exists (and, for a region source, its region can be extracted), its current pin equals the pinned value, and that pin is anchored, its path is one git ignores, or anchoring is not evaluated (see "Anchoring"). |
| `unpinned` | The source has no pin at all — no `blob` for a whole-file source, no `hash` for a region source (e.g. imported, or added by hand without running `verify`). |
| `unanchored` | The current pin equals the pinned value, but inside git that content was never committed or staged at that path — for a region source, see the anchoring fallback below (see "Anchoring"). |
| `stale` | The file exists (and, for a region source, its region can be extracted) but its current pin differs from the pinned value. |
| `missing` | The file does not exist, is not a regular file, or cannot be read at that path (deleted, a directory, or unreadable permissions) — or, for a region source, its region cannot be extracted (see "Regions" above for the reasons). |
| `renamed` | The file does not exist at the cited path, but git traces it to a new path (see "Renames" below); takes the place `missing` would otherwise read. |

A claim's overall state is the **worst of its sources' states**, in this
precedence (worst wins): `missing` > `renamed` > `stale` > `unanchored` >
`unpinned` > `fresh`. A claim with no sources at all is `fresh` by
convention — but `verified` claims are required to have at least one source
(see above), so this only arises for a hand-edited file that bypassed that
check.

`unpinned`, `unanchored`, `stale`, `missing` and `renamed` are collectively
`NON_FRESH`: these five are what `claimlock check` fails on, alongside any
`invalid` (including conflicted) claim. `owed` never fails `check`.

## Renames (git only)

For each cited path whose file does not exist — only these; the common path
(nothing missing) runs no extra git at all — `gitio.find_renames` looks for a
rename, in the project root:

1. Only when `<path>` is absent from HEAD:
   `git log -1 --format=%H --diff-filter=D -- <path>` names the commit `C`
   that last deleted it. If one exists, `git diff -M --name-status -z
   --diff-filter=R C^` — `C`'s parent diffed against the current working
   tree, over all tracked paths — is searched for a rename whose old path is
   `<path>`; the reported commit is `C`, shortened to 7 characters. Because
   the comparison runs from before the first deletion to the current working
   tree, a chain of renames (`a` → `b` → `c`) is reported end to end as `a` →
   `c`.
2. If that finds nothing, and only when `<path>` is absent from the index,
   `git diff -M --name-status -z --diff-filter=R --cached HEAD` is searched
   instead (a staged but uncommitted `git mv`); the reported commit is the
   literal string `uncommitted`.
3. A rename counts only when the new path exists right now and lies inside
   the project root.

A path present in both HEAD and the index is only deleted in the working
tree and is never a rename, even if an older commit once deleted it and it
was restored since. Every pathspec-taking call runs with
`--literal-pathspecs`, so a path like `src/[id].tsx` never matches
`src/i.tsx`.

Detection uses git's default similarity threshold (50%), so a rename with
edits is still found (and then reads `stale` after `follow` rewrites the
path). A plain `mv` that is neither committed nor staged is invisible to git
— it shows as a deletion plus an untracked file — and the source still reads
`missing`, not `renamed`.

A source whose file is missing and that `find_renames` maps to a new path
reads `renamed` instead of `missing` (see the state table above). `check`'s
per-source line for it reads `<key>: renamed → <new> (<sha7 | uncommitted>)`,
and the hint (also in `show`) is
`a source was renamed — run: claimlock follow <id>`. A `check --json` source
entry for it gains `"renamed_to": "<new-path>"`. `diff` prints
`--- <key>: renamed to <new> in <sha7 | uncommitted> — run: claimlock follow <id>`.

See `claimlock follow` below for how to act on it.

## `claimlock follow`

`claimlock follow <id>...` rewrites the `path` of every `renamed` source of
each named claim to its new path, keeping `region`, `blob` and `hash`
unchanged — works for any status, not only `verified`. If the claim's `pins`
digest matched its old sources exactly, it is recomputed over the new ones;
if it was mismatched or absent, it is left exactly as it was (never invented,
never dropped). Every other field is unchanged. The claim is then
re-evaluated and each followed source prints:

```
followed <id>: <old> → <new> (<new state>)
```

`<old>`/`<new>` are keys (`path`, or `path#region` for a region source);
`<new state>` is that source's freshness right after the rewrite — `fresh`
for an unchanged move, `stale` when the content changed in the same commit
or staging as the rename.

Refused with exit 1 (the file is left untouched):

- `no claim '<id>'`
- `<id> has merge conflicts — run claimlock resolve first`
- the claim's own parse error, if it has one
- `<id> has problems that must be fixed first (run: claimlock check)` — any
  problem `check` reports (a malformed region name, a `pins` value that is
  present but not a 40-hex digest, …), because rewriting the sources would
  silently repair or mangle it; the one exception is a mismatched-but-valid
  digest, which is fine and is left as is
- `follow needs a git repository`
- `<id>: no renamed sources`
- `<id>: <new-path> is already cited` — the new path (with the source's
  region, if any) would collide with a key another of the claim's sources
  already uses, including two renamed sources landing on the same new path

## The blob pin

A pin is a git blob id. How it is computed depends on where the store is:

- **Inside a git work tree** (`git rev-parse --is-inside-work-tree` is
  `true`), the pins a run needs are computed by one `git hash-object
  --stdin-paths`, which applies the file's clean filters and
  `text`/`eol`/`core.autocrlf` normalization. The pin is the blob git would
  store, so an LF checkout and a CRLF checkout of the same committed content
  agree. If that call fails for a file, the raw formula below is used instead.
- **Outside git**, the pin is computed without invoking git:

  ```
  sha1(b"blob " + str(len(data)).encode() + b"\0" + data)
  ```

  byte-identical to `git hash-object --no-filters <file>`.

Consequences:

- Where git applies no conversion to a file, both modes give the same pin, so
  a store created in a plain directory stays valid after `git init`. A file git
  does convert reads `stale` once after moving into git; one `verify` settles
  it.
- Pins still differ between clones when git would convert a file differently
  in them (for example content committed with CRLF bytes, checked out with
  `core.autocrlf=true` in one clone only).
- The stat cache (`.claimlock/cache/stat.json`) stores each entry with a tag
  and trusts it only under the same tag. Outside git the tag is `raw`. Inside
  git it is `git:` plus a digest of the contents of every setting that decides
  how git converts that file — the repository `config`, `config.worktree` and
  `info/attributes`; the global and system config and attributes files at their
  default locations or `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`; the git config
  environment variables; and each `.gitattributes` from the work tree top to the
  file's directory — read from disk with no git call. A config `include` target
  and a custom `core.attributesFile`'s contents are not part of it. `verify` and
  `resolve` always re-hash from disk.
- `claimlock diff` reads the pinned content from git only (`git cat-file blob
  <sha>`); there is no local copy. When git does not have it, `diff` says the
  prior content is unavailable (`--- <key>: changed, but the pinned content
  <blob12> is not in git (never committed, or no repository) — prior content
  unavailable; re-read the claim against the current file`). It works for a
  verified or an `owed` claim (an owed claim's pins are evaluated as if
  verified). Lines are compared without their endings, so a CRLF checkout of
  LF content diffs only the lines that changed; when only line endings
  differ, it prints `--- <key>: only line endings differ from the pinned
  content`. Lines break at `\n` only — a form feed or other Unicode line
  separator stays inside its line, as in git. A unified diff's headers are
  `<key> @ <pin12> (verified)` and `<key> (now)`. Each source's unified diff
  (headers included) is capped at `DIFF_LINES = 200` lines (the constant lives
  beside `check`'s and `show`'s in `lib/claimlock/cli.py`); when a source's
  diff holds more, the cut prints `… <n> more diff lines — claimlock diff <id>
  --full`, `<n>` the lines withheld for that source alone — a second stale
  source in the same claim is capped independently. `--full` restores every
  source's diff whole. A diff that fits under the cap prints exactly as
  before.
- For a **region** source, `diff` reads the pinned `blob` (the whole file, as
  it was verified) from git and extracts the region from it — if that fails,
  it prints `--- <key>: the region cannot be found in the pinned content
  (<reason>)` — then extracts the region from the current file the same way
  (the source's state is already `missing` when that fails, so this is a
  loud fallback, not the expected path) and diffs the two regions'
  text — never the whole file's. The same `DIFF_LINES` cap applies to a
  region's diff.
- A source reached through a symlink — the source itself, or a directory above
  it — pins the content at the **resolved** path. Git stores a symlink as its
  link text, so that content never appears at the cited path in history;
  anchoring therefore also looks at the resolved path (see Anchoring), and
  `diff` reads the pinned content from git like any other.
- A whitespace-only edit or a single re-saved byte produces a different pin
  and makes the claim stale. See "Limits" in the README.

## Anchoring

Inside git, a pin whose content matches is additionally required to be
**anchored**: every clone must be able to recover the content it pins. For the
set of cited source paths, one run computes the anchor set:

- every blob that appeared at exactly one of those paths in any commit
  reachable from any ref — full history (`git rev-list --objects --all
  --full-history`), including local branches and `refs/stash`; and
- every blob staged in the index for those paths, at every stage (`git
  ls-files -s`), so a conflicted merge's stages 1–3 count.

Both commands run with `--literal-pathspecs`, so a source named `src/[id].ts`
never matches `src/i.ts`. A pin is anchored when its blob is in that set — a
union across every cited path, since anchoring asks whether git can serve the
content by sha. A cited source whose fully resolved path differs from the cited
one — it is a symlink, or lies under a symlinked directory — and is a regular
file inside the root (found by resolving the path, no git) adds that resolved
root-relative path to the query, so committed content there anchors the pin. The index is read first;
history is walked only when some pin being checked is not staged. A
blob written to the object store by other means (`git hash-object -w`, or staged
and then unstaged) is not anchored.

Exemptions:

- A source path git ignores (`git check-ignore --stdin`) is exempt: content that
  can never be committed or staged is not owed a commit. A path starting with
  `:` is not recognised as ignored by that check, so it gets no exemption.
- When the store root itself lies inside a directory an enclosing repository
  ignores (`git check-ignore -q .`), anchoring is not evaluated at all.
- Outside git, or if the anchoring git call fails, anchoring is not evaluated.

Because local-only refs anchor, a pin can read `fresh` in a clone that has
content nobody else has yet; in CI it reads `stale` (CI's checkout does not
contain that content). A pre-commit `check` after `git add` reads fresh, because the
staged blob anchors.

**A region source anchors with a fallback.** It is anchored the same way as
above (via its whole-file `blob`) when that blob is in the anchor set or its
path is git-ignored; only when it **isn't** does the fallback run: the file's
currently staged content (index stage 0, read straight from git with no
commit needed) is fetched and the region is extracted from it — if that
still has the same region with the same `hash`, the pin is anchored. This
fallback runs only when the whole-file blob is unanchored, so the common
case (an already-anchored blob) never pays for the extra git read. Without
it, verifying a region and staging just that file, while other uncommitted
edits sit elsewhere in it, would leave the region pin `unanchored` until the
whole file was committed. Since `verify` writes the staged file's blob
whenever it holds the same region, the fallback matters mainly for pins
written before that rule.

## `claimlock check` output

Plain `check` prints a **bounded** report by default: each failing claim, then
the owed claims, then a `hints:` block, then a census line, and exits 1 if any
claim failed. `--full` (text or `--json`) restores the complete output; every
cut names what it withheld and that flag. Budget constants live in
`lib/claimlock/cli.py`: `LISTED_CLAIMS = 20` (failing/pre-existing/owed claims
listed), `SOURCE_LINES = 3` (per-source and per-problem detail lines per
claim).

```
INVALID  <id>
         <problem>
         <problem>
         <problem>
         … and <n> more problems — claimlock check --full
STALE    <id>
         <key>: stale
         <key>: stale
         <key>: stale
         … and <n> more sources — claimlock check --full
RENAMED  <id>
         <key>: renamed → <new> (<sha7 | uncommitted>)
… and <n> more failing claims — claimlock check --full
pre-existing (not changed here):
  <id>: stale
OWED     <id> → <owed_by> since <owed_since>, N commits ago
hints:
  stale: re-check it (claimlock diff <id>), then: claimlock verify <id>
  renamed: a source was renamed — run: claimlock follow <id>
claimlock: N claims, M sources hashed — A invalid, B unpinned, C unanchored, D stale, E missing, R renamed, F owed
```

- The state label is the state in capitals, padded to 8 characters
  (`UNANCHORED` is longer and is not truncated).
- **Hints print once, not per claim.** After every failing claim is listed
  (capped or not), a `hints:` block lists one line per state actually seen —
  in `invalid, unpinned, unanchored, stale, missing, renamed` order — as
  `  <state>: <hint text>`, with `<id>` in the hint text filled in from the
  *first* claim found in that state (not necessarily one of the claims
  printed, if the list was capped). `invalid` carries no hint (the printed
  `<problem>` lines are the detail); the hints are:
  - `unpinned`: `never pinned — re-check it, then: claimlock verify <id>`
  - `unanchored`: `the pinned content was never committed or staged — commit the source so every clone can see it (if it changed since, re-check, then: claimlock verify <id>)`
  - `stale`: `re-check it (claimlock diff <id>), then: claimlock verify <id>`
  - `missing`: `a source does not exist or cannot be read — fix its sources (or the file's permissions), re-check, then: claimlock verify <id>`
  - `renamed`: `a source was renamed — run: claimlock follow <id>`
  This applies with and without `--full` — it is pure duplication either way.
- A source's per-source line is `<key>: <state>` for every state except
  `renamed`, which instead prints
  `<key>: renamed → <new> (<sha7 | uncommitted>)` — the new path and, in
  parentheses, the 7-character commit that renamed it, or the literal
  `uncommitted` for a staged-but-uncommitted `git mv`. At most `SOURCE_LINES`
  of these per claim, then
  `         … and <n> more sources — claimlock check --full`; likewise at most
  `SOURCE_LINES` `<problem>` lines, then
  `         … and <n> more problems — claimlock check --full`. `--full` prints
  every source line and every problem.
- At most `LISTED_CLAIMS` failing claims are listed, then
  `… and <n> more failing claims — claimlock check --full`. The
  `pre-existing (not changed here):` list and the `OWED` lines are each capped
  the same way. `--full` lists every claim.
- An `OWED` line is printed for every owed claim without problems. `, N
  commits ago` (commits from `owed_since` to HEAD) is omitted when
  `owed_since` is `none` or cannot be counted; it is counted once per distinct
  `owed_since`. When the claim's pins, evaluated as if it were verified, are
  not fresh, the line ends with the worst source state: ` (unpinned)`,
  ` (unanchored)`, ` (stale)`, ` (missing)` or ` (renamed)`. This is a
  listing only: an owed claim never blocks and `--json` gives it
  `state: null`.
- `, F owed` is appended to the census only when F > 0. The other counts,
  `renamed` included, are always present, in that order, and count only the
  claims that block. The census always reports the full totals, uncapped,
  whether or not `--full` was given.
- "sources hashed" counts the source files looked at this run, including those
  answered from the stat cache.

### `--changed <base>`

1. The merge base of `<base>` and `HEAD` is found (`git merge-base <base>
   HEAD`); the changed paths are `git diff --name-only --relative --no-renames
   <merge-base> HEAD` — committed changes only, never the index or the working
   tree.
2. A claim is **in scope** when its claim file, or any of its cited source
   paths, is among the changed paths.
3. In-scope failing claims print as above and set exit 1. Out-of-scope failing
   claims are printed under one heading and never block:

   ```
   pre-existing (not changed here):
     <id>: invalid
     <id>: stale
   ```

4. Owed claims are listed as above and never block.
5. The census names the scope: `claimlock: N claims (K in scope), M sources
   hashed — …`.
6. Exit 2, printing only an error, when the store is not in a git repository
   (`--changed needs a git repository`), no merge base exists (`cannot find a
   merge base between '<base>' and HEAD`), or git fails to list the changed
   paths (`could not list changes since <merge-base> (git failed)`) — never an
   empty scope that would pass.

`check --json` prints one object: `claims`, `sources_hashed`, `counts` (six
blocking counts: `invalid`, `unpinned`, `unanchored`, `stale`, `missing`,
`renamed`), `scope` (sorted in-scope ids, or
`null` without `--changed`), `omitted`, and `results`, with `id`, `area`,
`status`, `problems`, `state`, `sources` (`path` — the source's **key**,
`path` or `path#region` — and `state`; a `renamed` entry gains
`renamed_to: "<new-path>"`), `in_scope`, `blocking` and `owed_by`.
`--area <a>` limits every output to that area.

By default `results` holds only **blocking** claims (those with `problems`, or
a non-fresh `state`) plus `owed` claims — a gate reader needs both, and a
fresh, non-owed claim carries nothing actionable. `omitted` is
`claims - len(results)`, the count of fresh claims left out. `--full` restores
every claim in `results` and sets `"omitted": 0`. `claims`, `sources_hashed`,
`counts` and `scope` are always the full totals, `--full` or not — only
`results` is filtered. This is a breaking change for a consumer that parsed
every claim from `results` before this existed; pass `--full` to get that
shape back.

## `claimlock search`, `claimlock show` and `claimlock list` output

Budget constants live beside `check`'s in `lib/claimlock/cli.py`: `BODY_LINES
= 40` (body lines printed by `show`), `EVIDENCE_CHARS = 200` (each evidence
`ref` printed by `show`), `HEADLINE_CHARS = 120` (headline printed by
`search` and `list`). Every claim's **headline** is the first non-blank line
of its body, stripped (`Claim.headline()`).

`search <query>` prints one line per hit by default, no body lines and no
blank separator:

```
<id> (<area>, <status>)[ [<state>]]  <headline, clipped to HEADLINE_CHARS>
```

`[<state>]` appears only when the claim's state is non-fresh (e.g. ` [stale]`),
immediately before the two spaces that separate the header from the headline.
A headline over `HEADLINE_CHARS` is cut to its first `HEADLINE_CHARS - 1`
characters plus a trailing `…` (`HEADLINE_CHARS` total). `--body` restores the
matching lines, indented four spaces under each hit, followed by a blank
line — today's uncapped shape:

```
<id> (<area>, <status>)[ [<state>]]
    <matching body line>
    <matching body line>

```

The no-match message (`claimlock: nothing matches '<query>'`, exit 1) and a
hit's exit 0 are unchanged either way.

`show <id>` is unchanged except for two caps, both restored whole by `--full`:
the status line, any `INVALID`/state line, the `Evidence:` kind labels, the
`Sources (a change here makes this claim stale):` block and the trailing
`file:` line are never truncated.

- The **body** is capped at `BODY_LINES` lines; when it holds more, the cut
  prints `… <n> more lines — read <claim path>` — the same relative path the
  trailing `file:` line names.
- Each evidence **`ref`** is clipped to `EVIDENCE_CHARS` characters (its first
  `EVIDENCE_CHARS - 1` plus a trailing `…` when longer) — the `[<kind>]` label
  before it is never clipped.

`list` is unchanged except that each claim's headline (the second, indented
line) is clipped to `HEADLINE_CHARS` the same way as `search`'s; `--full`
prints it whole. The status/mark and flag line is unchanged.

## `claimlock owe`

`claimlock owe <id>... [--to <email>] [--reason <text>]` rewrites each claim to
`status: owed` with `owed_by` (`--to`, else `git config user.email`) and
`owed_since` (HEAD's 7-character id, or `none`), leaves its pins and evidence
unchanged, and prints `owed <id> → <email> (since <owed_since>)`.

Refused with exit 1 (nothing written for that id):

- `no claim '<id>'`
- `--reason must be a single line`
- `<id> has problems that must be fixed first (run: claimlock check)` —
  including a conflicted claim
- `<id> is <unverified|refuted>; only a verified or owed claim can be owed`
- `'<email>' is not an email address`
- `<id> is fresh — nothing is owed` (a verified claim in any non-fresh state
  may be owed)
- `<id> is already owed by <email>`

With no `--to` and no git `user.email`, it exits 2: `nobody to hand this to —
pass --to <email> or set git config user.email`. Owing an owed claim to a
different person rewrites `owed_since` to the current HEAD.

`claimlock stale` lists owed claims as `<id>\t<area>\towed\t<owed_by>` beside
the non-fresh ones (`<id>\t<area>\t<state>\t<keys>`, comma-separated source
keys); it exits 1 only when a
non-fresh verified claim is listed. `--owed-by <email>` and `--mine` (your git
`user.email`; exit 2 if none is set) restrict `stale` and `list` to claims owed
by that person.

Every "owed to this person" comparison — `--owed-by`, `--mine`, `owe`'s
`already owed` refusal, and the hooks' owed-to-you notices — compares emails
stripped of surrounding spaces and casefolded, so `Bob@Example.com` is
`bob@example.com`. The value written into `owed_by` keeps the case given.

## `claimlock resolve`

`claimlock resolve [<id>...]` settles conflicted claims; with no ids it
processes every conflicted claim (and prints `claimlock: no conflicted claims`
when there are none). A named claim that is not conflicted prints `-      <id>
not conflicted`. Each processed claim prints `<OUTCOME> <id>  <message>`:

| Outcome | Rule | Message |
|---|---|---|
| `KEPT` | Every conflict hunk lies wholly inside the frontmatter `sources` block (its `pins:` line included), both sides cite the same **keys** (`path`, or `path#region`), and one side is whole: every key's current working-tree pin — a region hash for a region source, a blob for a whole file, both cache-bypassed — equals that side's pin for the same key, and that side's `pins:` digest, if it has one, matches its pins ("ours" is tried first). A side's pins and digest are read from its own version of the claim — index stage 2 (ours) or 3 (theirs) — because lines that merged cleanly from the other branch appear on both sides of the conflicted file; when git has no such stage, the side is read from the conflicted file and can be kept only if its digest matches. The claim is rewritten with that side's pins and digest; a side without a digest stays without one. A region entry's `blob` always travels with its `hash` from the same side — the two are never mixed from different sides. | `every source matches what one side verified` |
| `OWED` | As `KEPT`, but neither side is whole. Each key keeps the pin(s) of the side it matches (for a region entry, `blob` and `hash` together, from that same side), else the "ours" pin; the `pins:` line is removed (nobody verified that set as a whole), and the claim becomes `status: owed`, `owed_by` = git `user.email`, `owed_since` = HEAD. | `<why> — owed by <email>`, where `<why>` is `a source matches neither side`, or, when every source matches some side but no one side matches them all, `the sources match pins from two verifications that never checked them together` |
| `LEFT` | The file is not written. | one of the messages below |

`LEFT` messages:

- `<file>: a frontmatter delimiter is inside a conflict — needs a person`
- `<file>: a conflict outside the sources block (line N) needs a person` — a
  hunk in the body, the evidence or any other field; one conflict outside the
  block leaves the whole file
- `<file>: the two sides cite different sources — resolve by hand`
- `<file>: <why>, and there is no git user.email to record who owes the re-check — set one, then run claimlock resolve`
- `<file>: <why>, and git user.email '<email>' is not an email address to record who owes the re-check — fix it, then run claimlock resolve`
- `<file>: unterminated conflict hunk`, `<file>: conflict end marker without a
  start (line N)`, or a frontmatter parse error of one side

Hunks may include a diff3 base section (`||||||| `), which is discarded.
`resolve` never stages, commits or touches any file but the claim. It exits 1
when any claim is `LEFT` or a named id does not exist, and 0 otherwise.

## `claimlock who` and `claimlock show`

For each source, the verifier is the author email, ISO-8601 author time and
short id of the latest commit whose diff added or removed the exact pin line
in the claim file — `    blob: <sha>` for a whole-file source, `    hash:
<sha>` for a region source (`git log --follow -1 -G '^    <field>: <sha>$'
-- <claim file>`, so renames of the claim file are followed; `<field>` is
`blob` or `hash`, whichever the source is judged by — `gitio.verifier(root,
claim_rel, field, value)` takes the field name). `who` prints one
tab-separated line per source, keyed like every other per-source listing
(`path`, or `path#region`):

```
<key>	<email>	<iso time>	<short sha>
<key>	uncommitted          # no commit added that pin line
<key>	unknown              # not in a git repository
<key>	unpinned             # the source has no pin
```

`show` prints one line per source, in the same form `who` reads: `<key> —
<state> (<pin12>)` — for a `missing` region source followed by `: <reason>`
(e.g. `f.py#r — missing (587be6b4c3f9): region 'r' has no end marker`) —
with the same verifier note appended to the end of that line — `— verified by <email> at <time> (<sha>)`, `— uncommitted (verifier
known once committed)`, or `— verified by unknown (no git)`. The pin shown
is `hash` for a region source, `blob` otherwise, truncated to its first 12
characters (`unpinned` if there is none). Each source's state
column is its freshness; for an `owed` claim the pins are evaluated as if it
were verified, so the recipient sees which sources moved (`check` still
gives an owed claim no verdict). Because attribution is by pin line, a
commit that only reorders `sources:` is credited, and a claim file committed
with CRLF line endings reads `uncommitted`. `who`, `show` and `diff` exit 1
for an id with no claim.

## `.claimlock.toml`

All keys are optional; a key present but misspelled is a hard error (`unknown
key(s) [...]`) rather than a silently-ignored typo. Every key has a default,
used when the whole file — or the key — is absent:

| Key | Type | Default |
|---|---|---|
| `claims_dir` | string | `"claims"` (resolved relative to the project root; must stay inside the root) |
| `marker_globs` | list of strings | `["**/*.md"]` |
| `marker_pattern` | string (regex, must have ≥ 1 capture group) | `` Claim: `([a-z0-9][a-z0-9-]*)` `` |

The project root is the nearest ancestor directory containing
`.claimlock.toml`; if none exists, the root falls back to `git rev-parse
--show-toplevel` (or the starting directory itself if git is unavailable or
the directory isn't a repository). A project with neither a config file nor a
`claims_dir` directory `has_store() == False`.

The hooks are stricter than the CLI: they are active only when
`.claimlock.toml` exists in the opened project directory or one of its
ancestors. A `claims_dir` directory alone does not activate them, and a config
in a subdirectory of the opened project is not seen.

## Hook messages

Three hooks ship with the plugin; all exit 0 and never set a blocking
decision. Each message is capped at 2,000 characters.

- **Session start** (Claude sees it, as context): first, claims owed to your
  git `user.email`, if any (nothing shown if no email is set); then counts of
  invalid, conflicted, unpinned, unanchored, stale, missing and owed claims
  and of dangling markers, in that order, with the affected areas.
- **After a Bash or MCP tool call** (Claude sees it, as context), only when
  HEAD has moved (commit, merge, rebase, pull, checkout): claims that became
  owed to you and claim files with merge conflicts (`claimlock resolve`)
  first; then up to 10 claims, backed by files changed anywhere in the commit
  range, that are now not fresh — each naming who changed the source and in
  which commit; then up to 10 markers naming no claim. `…` after the claims
  means there are more — run `claimlock stale`; `…` after the markers means
  there are more — run `claimlock refs`.
- **End of turn** (only the user sees it, as a `systemMessage`): problems not
  present at the last check in this clone — a baseline taken at session
  start, then replaced by each end-of-turn check ("since the last check"; a
  problem already reported is not repeated), separating drift from
  uncommitted edits to cited sources from drift that arrived another way
  (e.g. a `git pull`), plus a HEAD-moved report no tool call delivered; a
  claim the HEAD-moved report already named in the same state is not listed
  twice. During an in-progress merge, rebase or cherry-pick, end of turn does
  not label any drift "your uncommitted edits". A `git pull` that stops on
  conflicts does not move HEAD, so its conflicted claims reach end of turn
  ("became conflicted … run `claimlock resolve`"), not the after-tool-call
  hook.

Caps: the HEAD-moved report names at most 10 claims and at most 10 markers;
the end-of-turn report lists at most 5 per category. What a report could not
show (past its cap) is reported again at the next occurrence. Hook runs in
one session are serialised; a run that cannot take the session lock within
5 s is skipped without output. Internal errors, and hook input that was not
valid JSON, are recorded in `hook-errors.log` in the plugin data directory.

## Marker scanning (`claimlock refs`)

A **marker** is any regex match of `marker_pattern` in any file matched by
`marker_globs`, read as UTF-8. The default pattern captures the claim id from
`` Claim: `some-id` `` written inline in prose.

Excluded from every scan, unconditionally:
- Any path component starting with `.` (hidden directories) or named
  `node_modules`.
- Inside a git work tree, any file git ignores: candidates come from `git
  ls-files --cached --others --exclude-standard` run at the project root
  (tracked plus untracked-but-not-ignored files; submodule contents are not
  listed). Outside git, if that command fails, or when the store root itself
  lies inside a directory an enclosing repository ignores, the tree is walked
  instead.
- Anything inside the resolved `claims_dir` itself (so a claim file quoting
  its own marker syntax in an example doesn't self-match).
- When `refs.scan(project, only={...})` is called with an explicit path set
  (used by the PostToolUse hook to scan only what a commit touched), a path
  outside the project root, or one that does not match `marker_globs`, or one
  that is otherwise excluded, is silently skipped rather than raising.
- A matching file that is not valid UTF-8, or that cannot be read at all
  (permissions, a race with deletion), is skipped and **not** counted in the
  scanned-files total — `scan`'s per-file `scanned += 1` only runs after a
  successful read, so such a file contributes neither markers nor a count.

A marker naming an id with no matching claim file is **dangling**; `claimlock
refs` prints each one and exits 1 if any exist, 0 otherwise.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean: no blocking claims (for `check`; `owed` never blocks), no non-fresh verified claims (for `stale`), no dangling markers (for `refs`), no `LEFT` outcome (for `resolve`), and successful read-only commands. |
| `1` | Findings or a refusal: `check` found a blocking claim; `stale` found a non-fresh verified claim; `refs` found a dangling marker; `search` found nothing; `show`/`who`/`diff` named no claim; `import` reported per-file errors; `verify`, `follow`, `owe`, `new` or `init` was refused; `resolve` left a claim or named no claim. |
| `2` | Cannot run: bad `.claimlock.toml`, no claims directory, or a claims directory that cannot be listed; `init`/`import` into a target directory that doesn't exist; `check --changed` outside git, with no merge base, or when git fails to list the changes; `owe` with no `--to` and no git `user.email`; `--mine` with no git `user.email`. |

`claimlock hook <event>` **always exits 0** — a hook must never fail the
tool call that invoked it (see `docs/hook-semantics.md`). That includes a
`python3` older than 3.11: the launcher checks for `hook` before its version
check, appends one line to `$CLAUDE_PLUGIN_DATA/hook-errors.log`, prints
nothing and exits 0. (If `python3` itself is missing, claimlock never runs:
the shell's own non-zero exit is outside what claimlock can control.)
