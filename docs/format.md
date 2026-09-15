# Claim format reference

This is the normative reference, derived from the implementation
(`lib/claimlock/claims.py`, `lib/claimlock/frontmatter.py`,
`lib/claimlock/pins.py`, `lib/claimlock/gitio.py`, `lib/claimlock/ops.py`,
`lib/claimlock/merge.py`, `lib/claimlock/cli.py`, `lib/claimlock/project.py`).
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
| `pins` | string | Optional. The digest of the whole pin set, written by `claimlock verify` directly after the `sources` block: sha1 of the JSON array of `[path, blob]` pairs sorted by path (an unpinned source's blob is `""`). Must be 40 lowercase hex and equal that digest of the listed sources, else the claim is invalid; reordering sources keeps it. A claim with no `pins` line (verified before it existed) is valid. Every `verify` rewrites this one line, so two branches that re-verify one claim to different contents conflict on it even when they changed different sources. |

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
```

- `path` is required and must be a project-root-relative POSIX path that
  stays inside the project root: no leading `/`, no `\`, no Windows drive
  letter, no `..` segment. A path that fails this is
  `source path '<p>' must be relative and stay inside the project root`.
- `blob`, if present, must be 40 lowercase hex characters, or
  `source '<p>' has a malformed blob (expected 40 lowercase hex)`.
- Any other key on a source map is `source '<p>' has unknown keys [...]`.
- The same `path` listed twice is `source '<p>' is listed twice`.
- A source entry with no usable `path` at all is
  `source entry needs a 'path': <repr>`.

`verify` and `resolve` write each pin as a map whose `blob:` line is exactly
four spaces, `blob: `, and the 40-hex id — the line `claimlock who` searches
history for.

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
- `source path '<p>' must be relative and stay inside the project root`
- `source '<p>' is listed twice`
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

## Freshness: the five states

Freshness is evaluated only for a claim whose `status` is `verified`, that is
not conflicted, and whose frontmatter parses cleanly; every other claim's
freshness is `None`. An `unverified`, `refuted` or `owed` claim, or an invalid
one, is neither fresh nor stale — it simply isn't being compared with its
sources.

For each source of a verified claim (a source whose path fails the
root-relative rule is skipped here; `problems()` reports it):

| State | Meaning |
|---|---|
| `fresh` | The file exists, its current pin equals the pinned `blob`, and that pin is anchored, its path is one git ignores, or anchoring is not evaluated (see "Anchoring"). |
| `unpinned` | The source has no `blob` at all (e.g. imported, or added by hand without running `verify`). |
| `unanchored` | The current pin equals the pinned `blob`, but inside git that content was never committed or staged at that path (see "Anchoring"). |
| `stale` | The file exists but its current pin differs from the pinned `blob`. |
| `missing` | The file does not exist, is not a regular file, or cannot be read at that path (deleted, renamed, a directory, or unreadable permissions). An unreadable source is reported this way, never raised, so one bad file cannot hide every other claim's state. |

A claim's overall state is the **worst of its sources' states**, in this
precedence (worst wins): `missing` > `stale` > `unanchored` > `unpinned` >
`fresh`. A claim with no sources at all is `fresh` by convention — but
`verified` claims are required to have at least one source (see above), so
this only arises for a hand-edited file that bypassed that check.

`unpinned`, `unanchored`, `stale` and `missing` are collectively `NON_FRESH`:
these four are what `claimlock check` fails on, alongside any `invalid`
(including conflicted) claim. `owed` never fails `check`.

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
  prior content is unavailable. It works for a verified or an `owed` claim (an
  owed claim's pins are evaluated as if verified). Lines are compared without
  their endings, so a CRLF checkout of LF content diffs only the lines that
  changed; when only line endings differ, it prints `--- <path>: only line
  endings differ from the pinned content`. Lines break at `\n` only — a form
  feed or other Unicode line separator stays inside its line, as in git.
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

## `claimlock check` output

Plain `check` prints each failing claim, then the owed claims, then a census
line, and exits 1 if any claim failed:

```
INVALID  <id>
         <problem>
STALE    <id>
         <path>: stale
         re-check it (claimlock diff <id>), then: claimlock verify <id>
OWED     <id> → <owed_by> since <owed_since>, N commits ago
claimlock: N claims, M sources hashed — A invalid, B unpinned, C unanchored, D stale, E missing, F owed
```

- The state label is the state in capitals, padded to 8 characters
  (`UNANCHORED` is longer and is not truncated). Each non-fresh source is
  listed, then one hint for the claim's overall state:
  - `unpinned`: `never pinned — re-check it, then: claimlock verify <id>`
  - `unanchored`: `the pinned content was never committed or staged — commit the source so every clone can see it (if it changed since, re-check, then: claimlock verify <id>)`
  - `stale`: `re-check it (claimlock diff <id>), then: claimlock verify <id>`
  - `missing`: `a source does not exist or cannot be read — fix its sources (or the file's permissions), re-check, then: claimlock verify <id>`
- An `OWED` line is printed for every owed claim without problems. `, N
  commits ago` (commits from `owed_since` to HEAD) is omitted when
  `owed_since` is `none` or cannot be counted; it is counted once per distinct
  `owed_since`. When the claim's pins, evaluated as if it were verified, are
  not fresh, the line ends with the worst source state: ` (unpinned)`,
  ` (unanchored)`, ` (stale)` or ` (missing)`. This is a listing only: an owed
  claim never blocks and `--json` gives it `state: null`.
- `, F owed` is appended to the census only when F > 0. The other counts are
  always present, in that order, and count only the claims that block.
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

`check --json` prints one object: `claims`, `sources_hashed`, `counts` (the
five blocking counts), `scope` (sorted in-scope ids, or `null` without
`--changed`), and `results`, one per claim, with `id`, `area`, `status`,
`problems`, `state`, `sources` (`path`, `state`), `in_scope`, `blocking` and
`owed_by`. `--area <a>` limits every output to that area.

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
the non-fresh ones (`<id>\t<area>\t<state>\t<paths>`); it exits 1 only when a
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
| `KEPT` | Every conflict hunk lies wholly inside the frontmatter `sources` block (its `pins:` line included), both sides cite the same paths, and one side is whole: every source's current working-tree pin (cache bypassed) equals that side's pin, and that side's `pins:` digest, if it has one, matches its pins ("ours" is tried first). A side's pins and digest are read from its own version of the claim — index stage 2 (ours) or 3 (theirs) — because lines that merged cleanly from the other branch appear on both sides of the conflicted file; when git has no such stage, the side is read from the conflicted file and can be kept only if its digest matches. The claim is rewritten with that side's pins and digest; a side without a digest stays without one. | `every source matches what one side verified` |
| `OWED` | As `KEPT`, but neither side is whole. Each source keeps the pin of the side it matches, else the "ours" pin; the `pins:` line is removed (nobody verified that set as a whole), and the claim becomes `status: owed`, `owed_by` = git `user.email`, `owed_since` = HEAD. | `<why> — owed by <email>`, where `<why>` is `a source matches neither side`, or, when every source matches some side but no one side matches them all, `the sources match pins from two verifications that never checked them together` |
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
short id of the latest commit whose diff added or removed the exact line
`    blob: <sha>` in the claim file (`git log --follow -1 -G '^    blob: <sha>$'
-- <claim file>`, so renames of the claim file are followed). `who` prints one
tab-separated line per source:

```
<path>	<email>	<iso time>	<short sha>
<path>	uncommitted          # no commit added that pin line
<path>	unknown              # not in a git repository
<path>	unpinned             # the source has no pin
```

`show` prints the same after each source: `— verified by <email> at <time>
(<sha>)`, `— uncommitted (verifier known once committed)`, or `— verified by
unknown (no git)`. Each source's state column is its freshness; for an `owed`
claim the pins are evaluated as if it were verified, so the recipient sees
which sources moved (`check` still gives an owed claim no verdict). Because attribution is by pin line, a commit that only
reorders `sources:` is credited, and a claim file committed with CRLF line
endings reads `uncommitted`. `who`, `show` and `diff` exit 1 for an id with no
claim.

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
| `1` | Findings or a refusal: `check` found a blocking claim; `stale` found a non-fresh verified claim; `refs` found a dangling marker; `search` found nothing; `show`/`who`/`diff` named no claim; `import` reported per-file errors; `verify`, `owe`, `new` or `init` was refused; `resolve` left a claim or named no claim. |
| `2` | Cannot run: bad `.claimlock.toml`, no claims directory, or a claims directory that cannot be listed; `init`/`import` into a target directory that doesn't exist; `check --changed` outside git, with no merge base, or when git fails to list the changes; `owe` with no `--to` and no git `user.email`; `--mine` with no git `user.email`. |

`claimlock hook <event>` **always exits 0** — a hook must never fail the
tool call that invoked it (see `docs/hook-semantics.md`). That includes a
`python3` older than 3.11: the launcher checks for `hook` before its version
check, appends one line to `$CLAUDE_PLUGIN_DATA/hook-errors.log`, prints
nothing and exits 0. (If `python3` itself is missing, claimlock never runs:
the shell's own non-zero exit is outside what claimlock can control.)
