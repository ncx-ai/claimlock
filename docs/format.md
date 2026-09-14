# Claim format reference

This is the normative reference, derived from the implementation
(`lib/claimlock/claims.py`, `lib/claimlock/frontmatter.py`,
`lib/claimlock/project.py`). If this document and the code ever disagree, the
code is right and this file has drifted — file an issue.

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

Blank lines and full-line comments (`#...` after stripping) are always
allowed between keys and inside list blocks.

## Fields

| Field | Type | Rule |
|---|---|---|
| `id` | string | Required. Must match `^[a-z0-9][a-z0-9-]*$` (kebab-case) and equal the filename stem (`retries-are-capped.md` → `id: retries-are-capped`). Missing `id` → `missing 'id'`. |
| `area` | string | Optional; defaults to `"unfiled"` if absent. Must be a single scalar, not a list. |
| `status` | string | One of `verified`, `unverified`, `refuted`. Defaults to `"unverified"` if absent. |
| `verified_at` | string | Optional; an ISO-8601 timestamp written by `claimlock verify`. Must be a single scalar. Not otherwise validated as a date. |
| `evidence` | list | A list of `{kind, ref}` maps. `kind` must be one of `test`, `measurement`, `source`, `run`. No other keys are allowed on an evidence entry. |
| `sources` | list | A list of source entries — see below. |

Any frontmatter key outside this set is `unknown field '<k>' (allowed: id, area, status, verified_at, evidence, sources)`.

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
Empty body → `no claim text after the frontmatter`.

## Every message `problems()` can emit

`claims.py::problems` returns every reason a claim cannot be trusted as
written; it does not stop at the first one. The complete set of message
*prefixes* (some interpolate the offending value):

- A parse error short-circuits everything else and is the claim's only
  problem: `<file>:<line>: <message>` — see "Parse errors" below.
- `unknown field '<k>' (allowed: id, area, status, verified_at, evidence, sources)`
- `missing 'id'`
- `id '<id>' is not kebab-case ([a-z0-9][a-z0-9-]*)`
- `id '<id>' does not match filename '<file>'`
- `'<area|verified_at>' must be a single value`
- `status '<s>' is not one of verified, unverified, refuted`
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
- `status is 'verified' but no evidence is cited`
- `status is 'verified' but no sources are listed, so it can never go stale`

The last two only fire when checking (or attempting to set) `status:
verified` — `claimlock verify` runs `problems()` with `as_status="verified"`
so a claim with no evidence or sources yet is refused verification even while
it is still nominally `unverified` on disk.

### Parse errors (frontmatter is unreadable at all)

A parse error makes the whole claim `invalid`; no other check runs, because a
file whose shape could not be understood cannot be trusted to report its own
other problems correctly. Reported as `<file>:<line>: <message>`:

- `file must start with a '---' line` (line 1)
- `no closing '---' line` (line 1)
- `CR line endings are not supported; convert to LF` (line 1 — checked before
  frontmatter parsing; the file is read as raw bytes specifically so a `\r`
  survives to be caught here instead of being silently normalized away)
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

## Freshness: the four states

Freshness is evaluated only for a claim whose `status` is `verified` and whose
frontmatter parses cleanly; every other claim's freshness is reported as
`None` — an `unverified` or `refuted` claim, or an invalid one, is neither
fresh nor stale, it simply isn't being checked yet.

For each source of a verified claim:

| State | Meaning |
|---|---|
| `fresh` | The file exists and its current blob hash equals the pinned `blob`. |
| `unpinned` | The source has no `blob` at all (e.g. imported, or added by hand without running `verify`). |
| `stale` | The file exists but its current blob hash differs from the pinned `blob`. |
| `missing` | The file does not exist or cannot be read at that path (deleted, renamed, no longer a regular file, or unreadable permissions). An unreadable source is reported this way, never raised, so one bad file cannot hide every other claim's state. |

A claim's overall state is the **worst of its sources' states**, in this
precedence (worst wins): `missing` > `stale` > `unpinned` > `fresh`. A claim
with no sources at all is `fresh` by convention (there's nothing that could
have gone stale) — but note `verified` claims are required to have at least
one source (see above), so this only arises for a hand-edited file that
bypassed that check.

`unpinned`, `stale` and `missing` are collectively `NON_FRESH`: these three
are what `claimlock check` fails on, alongside any `invalid` claim.

## The blob pin

A pin is a git blob SHA1, computed **without invoking git**:

```
sha1(b"blob " + str(len(data)).encode() + b"\0" + data)
```

This is byte-identical to `git hash-object --no-filters <file>`. Consequences:

- A store can be created and verified in a plain directory with no `.git` at
  all; running `git init` afterward does not invalidate any existing pin,
  because the hash never depended on git being present.
- Where a repository does exist, `claimlock diff` can ask git for a blob's
  content directly (`git cat-file blob <sha>`) instead of needing its own
  copy.
- A source that is a symlink (to a file inside the root) pins the **target's**
  content, because the file is read through the link. Git stores a symlink's
  link text as its blob, not the target's bytes, so `claimlock diff` will not
  find that pinned blob in git; it falls back to the snapshot under
  `.claimlock/objects/`, or reports the prior content unavailable in a clone
  that has no snapshot.
- The formula hashes exact bytes: a whitespace-only edit, a line-ending
  change, or a single re-saved byte all produce a different hash and make
  the claim stale. See "Limits" in the README.

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
  listed). Outside git, or if that command fails, the tree is walked instead.
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
| `0` | Clean: no invalid claims, no non-fresh claims (for `check`/`stale`), no dangling markers (for `refs`), successful read-only commands. |
| `1` | Findings: `check` found an invalid or non-fresh claim; `stale` found a non-fresh claim; `refs` found a dangling marker; `search`/`show` found nothing to show; `import` reported per-file errors; `verify` was refused for at least one id. |
| `2` | The store could not be read at all: bad `.claimlock.toml`, or no `claims/` directory (`init`/`import` into a target directory that doesn't exist also exit 2). |

`claimlock hook <event>` **always exits 0** — a hook must never fail the
tool call that invoked it (see `docs/hook-semantics.md`). That includes a
`python3` older than 3.11: the launcher checks for `hook` before its version
check, appends one line to `$CLAUDE_PLUGIN_DATA/hook-errors.log`, prints
nothing and exits 0. (If `python3` itself is missing, claimlock never runs:
the shell's own non-zero exit is outside what claimlock can control.)
