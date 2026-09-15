# claimlock region pins and rename following — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a claim pin a marked region of a file instead of the whole file, and report a renamed source with its new path plus a `claimlock follow` command that updates the claim.

**Architecture:** A new pure module `lib/claimlock/regions.py` extracts marker-delimited regions from bytes. `claims.Source` gains `region` and `hash`; freshness, digest, verify, diff, show, who and resolve become key-aware (`path` or `path#region`). Rename detection is one new git helper, `gitio.find_renames`, called only for missing sources; a new `renamed` state and `ops.follow` build on it.

**Tech Stack:** Python ≥ 3.11 standard library, git, `unittest`.

**Spec:** `docs/specs/2026-09-15-claimlock-regions-renames-design.md` — read all of it before any task. Base specs: `docs/specs/2026-09-14-claimlock-design.md`, `docs/specs/2026-09-14-claimlock-teams-design.md` (amendments T1–T15).

## Global Constraints

- Python ≥ 3.11 standard library only in `lib/`; do not touch `bin/claimlock`.
- Tests: `python3 -m unittest discover -s tests` from the repo root must pass; git-dependent tests run (not skip) when git is installed. Report the observed total — the suite is **252** at the start of this plan.
- A claim reads `fresh` only when every cited source's current content (region text for a region source) equals its pin. No change may create a false `fresh`.
- Every git subprocess lives in `lib/claimlock/gitio.py` and degrades to None / False / [] / {} on failure, never raises.
- Hook PostToolUse with HEAD unchanged must not invoke git. Hooks always exit 0; output ≤ 2,000 characters.
- `verify` and `resolve` bypass the stat cache.
- Existing `pins:` digests of whole-file sources must stay valid (entry `[path, blob or ""]` unchanged).
- `NON_FRESH` order is `("unpinned", "unanchored", "stale", "missing", "renamed")`; severity is `missing` > `renamed` > `stale` > `unanchored` > `unpinned` > `fresh`.
- The README must list every command in `claimlock --help` as `claimlock <cmd>` (`tests/test_plugin_manifest.py`); the task adding `follow` adds its README row.
- No machine-specific absolute paths in tracked files; skills stay generic (`tests/test_skills_format.py`).
- Revert temporary mutations by re-applying the inverse edit, never `git checkout --`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv
  ```

## File map

| File | Change |
|---|---|
| `lib/claimlock/regions.py` | NEW: `NAME_RE`, `RegionError`, `extract`, `region_hash` |
| `lib/claimlock/claims.py` | `Source.region/hash/key/pin`; problems; freshness (regions, renamed); `pin_digest` entries; `renames_for`; `Result.renames`; `NON_FRESH` |
| `lib/claimlock/pins.py` | `Hasher.region` (cached) |
| `lib/claimlock/frontmatter.py` | `_sources_block` writes `region`/`hash` |
| `lib/claimlock/ops.py` | `verify` region-aware; NEW `follow` |
| `lib/claimlock/gitio.py` | `verifier(root, claim_rel, field, value)`; NEW `index_blob`, `find_renames` |
| `lib/claimlock/merge.py` | key-aware sides |
| `lib/claimlock/cli.py` | verify/diff/show/who/check/stale/json output by key; renamed hints; NEW `follow` command |
| `lib/claimlock/hooks.py` | only what `renamed` in `NON_FRESH` requires (none expected) |
| `lib/claimlock/selftest.py` | region and rename probes |
| `tests/test_regions.py` | NEW (Tasks 1–2) |
| `tests/test_renames.py` | NEW (Task 3) |
| `README.md`, `docs/format.md`, `skills/using-claimlock/SKILL.md`, `skills/operating-claimlock/SKILL.md` | Task 4 (plus the `follow` README row in Task 3) |

## Shared interfaces (exact names)

```python
# regions.py
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
class RegionError(Exception)          # str(e) is the reason text from spec §2.1
def extract(data: bytes, name: str) -> str   # region text (§2.2); raises RegionError
def region_hash(text: str) -> str             # pins.blob_of_bytes(text.encode("utf-8"))

# claims.py
@dataclass class Source: path: str; blob: str | None; region: str | None = None; hash: str | None = None
    key -> str        # path, or f"{path}#{region}"
    pin -> str | None # hash for a region source, else blob
def pin_digest(sources) -> str   # iterable of Source; whole-file entry [path, blob or ""],
                                 # region entry [path, region, blob or "", hash or ""]; sorted; sha1 of json.dumps
NON_FRESH = ("unpinned", "unanchored", "stale", "missing", "renamed")
@dataclass class Result: claim; problems; state; per_source; renames: dict = field(default_factory=dict)
    # per_source: [(key, state)]; renames: {path: (new_path, sha7 | "uncommitted")} for this claim's renamed sources
def renames_for(project, sources) -> dict[str, tuple[str, str]]   # {} outside git or when nothing is missing
def freshness(claim, project, hasher, anchors=None, as_status=None, renames=None) -> (state, [(key, state)])

# pins.py
Hasher.region(self, rel: str, name: str, use_cache=True) -> tuple[str | None, str | None]   # (hash, reason)

# gitio.py
def verifier(root, claim_rel, field, value)      # field "blob" or "hash"; matches line f"    {field}: {value}"
def index_blob(root, rel) -> str | None          # stage-0 blob id of rel, or None
def find_renames(root, rels) -> dict[str, tuple[str, str]]

# ops.py
def verify(project, cid) -> list[tuple[str, str]]              # [(key, pin)]
def follow(project, cid) -> list[tuple[str, str, str]]         # [(old_key, new_key, new_state)]
```

---

### Task 1: Region pins — extraction, claim format, freshness, verify, digest

**Files:**
- Create: `lib/claimlock/regions.py`, `tests/test_regions.py`
- Modify: `lib/claimlock/claims.py`, `lib/claimlock/pins.py`, `lib/claimlock/frontmatter.py`, `lib/claimlock/ops.py`, `lib/claimlock/merge.py` (only the `pin_digest` call signature), `lib/claimlock/cli.py` (only `cmd_verify` output and any place that indexes per-source state by `path` and must now use `key`)

**Interfaces:** Produces `regions.*`, `Source.region/hash/key/pin`, `pin_digest(sources)`, `Hasher.region`, region-aware `freshness` and `verify`. Consumes existing `pins.blob_of_bytes`, `frontmatter.rewrite(sources=, pins=)`.

Behaviour is spec §2.1–§2.5 and §2.6 (verify only). Anchoring fallback, diff, show, who and resolve are Task 2 — in this task a region source's anchoring uses only the existing `Anchors.ok(path, blob)`.

- [ ] **Step 1: Write failing tests** in `tests/test_regions.py`:
  - `extract`:
    - `b"a\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nz\n"` → `"x\ny\n"`
    - CRLF input gives the same text as LF input
    - an empty region (begin immediately followed by end) → `""`
    - marker inside `// claimlock:begin r1` and `<!-- claimlock:end r1 -->` comment syntax works
    - `claimlock:begin r1-extra` does NOT open `r1`
    - nested regions `outer` containing `inner`: `extract(..., "outer")` includes the inner marker lines; `extract(..., "inner")` excludes them
    - each failure raises `RegionError` with exactly the reason text from spec §2.1 (not found; begins more than once; has no end marker; ends before it begins; `b"\xff"` → not UTF-8)
  - `problems` (via `claims.load_claims` + `claims.problems`):
    - malformed region name (`Bad_Name`)
    - malformed hash
    - hash without region
    - region with blob but no hash (message `source 'a.py#r1' must pin both blob and hash`)
    - same (path, region) twice → `source 'a.py#r1' is listed twice`
    - the same path once whole and once with a region is valid
    - unknown key `color` still reported
  - Plain-directory store (`make_repo(use_git=False)`), claim with `sources: [{path: a.py, region: r1}]`:
    - `claimlock verify c` exits 0, prints `  a.py#r1 @ <hash[:12]>`, and writes `region`, `blob`, `hash` (order: path, region, blob, hash) and a `pins:` digest equal to `pin_digest` of the written sources
    - `check` exits 0; an edit outside the region (same file, different length) keeps `check` exit 0 and the state `fresh`
    - an edit inside the region → exit 1, `STALE`, per-source line `a.py#r1: stale`
    - removing the end marker → `MISSING`
    - `verify` on a missing region exits 1 with `c: source a.py#r1: region 'r1' has no end marker` on stderr and leaves the file unchanged
  - `pin_digest`:
    - a whole-file source list gives the same value as before this task, computed as `sha1(json.dumps([["a.py", "<blob>"]]))`
    - a region entry changes the digest
    - order-independent
  - Cache: a `Hasher` (raw mode) with a cached region entry (key `"a.py\0r1"`, `[size, mtime_ns, hash, "raw"]`, mtime ≥ 2 s old) returns the cached hash without reading the file; `use_cache=False` re-reads it.
- [ ] **Step 2: Run** `cd tests && python3 -m unittest test_regions -v` — expect import/attribute failures.
- [ ] **Step 3: Implement.**
  - `regions.py` per the interfaces.
  - `Source` fields and properties; `Claim.sources` reads `region`/`hash` keys.
  - `problems` per §2.3, including the duplicate check by `(path, region)` and the allowed-keys set `{path, blob, region, hash}`.
  - `Hasher.region(rel, name, use_cache)`:
    - `stat` the file (not a regular file / OSError → `(None, "does not exist or cannot be read")`).
    - Cache key `f"{rel}\0{name}"`, same entry format, racy guard and `_tag(rel)` as `blob`.
    - Otherwise read the bytes, `regions.extract`, `region_hash`; `RegionError` → `(None, str(e))` and nothing cached.
    - Increments `hashed`.
  - `freshness`: for a region source use `hasher.region`; states per §2.4 minus the git fallback; per-source entries are `(s.key, state)`.
  - `pin_digest(sources)` takes `Source` objects; update both callers (`ops.verify`, and `claims.problems`' digest check). `merge.resolve_claim` passes its `(path, blob)` tuples as `Source(path, blob)` for now (Task 2 makes merge region-aware).
  - `frontmatter._sources_block` writes `region` then `blob` then `hash` when present.
  - `ops.verify` returns `[(key, pin)]`, computing blob (`use_cache=False`) and, for region sources, `hasher.region(..., use_cache=False)`, refusing per §2.6.
  - `cli.cmd_verify` prints `  {key} @ {pin[:12]}`.
  - Grep `cli.py` and `hooks.py` for uses of `per_source` / `states.get(s.path)` and switch them to keys where they index per-source state.
- [ ] **Step 4: Run** the focused tests, then the full suite; fix regressions (existing tests that assert `per_source` paths for whole-file sources are unchanged because key == path).
- [ ] **Step 5: Commit** `feat(regions): marker-delimited region pins — extraction, format, freshness, verify, digest`.

### Task 2: Region pins — anchoring fallback, diff, show, who, resolve

**Files:**
- Modify: `lib/claimlock/claims.py` (anchoring), `lib/claimlock/gitio.py` (`index_blob`, `verifier` signature), `lib/claimlock/cli.py` (`cmd_diff`, `cmd_show`, `_verified`, `cmd_who`), `lib/claimlock/merge.py`
- Test: `tests/test_regions.py` (add git-backed classes), and update any existing test calling `gitio.verifier` with the old signature

**Interfaces:** Consumes Task 1's `Source`, `Hasher.region`, `regions.extract`, `pin_digest(sources)`. Produces `gitio.index_blob`, `gitio.verifier(root, claim_rel, field, value)`.

Behaviour: spec §2.4 (anchoring fallback) and §2.6 (diff, show, who, resolve).

- [ ] **Step 1: Write failing tests** (git repos via `helpers.make_repo(use_git=True)` / `clone`):
  - **Anchoring fallback.** Commit `a.py` containing region `r1` and the claim. Edit a line *outside* the region without committing or staging, then `verify`: the pinned blob is unanchored, but `check --json` state is `fresh` because the staged version contains the same region text. Control: edit *inside* the region, `verify` without staging → `unanchored`; `git add a.py` → `fresh`.
  - **`diff`.**
    - A stale region prints a unified diff whose `---`/`+++` headers are `a.py#r1 @ <hash12> (verified)` / `a.py#r1 (now)` and whose body contains only region lines (a changed line outside the region does not appear).
    - A removed end marker prints `--- a.py#r1: region 'r1' has no end marker`.
    - A whole-file source's diff output is unchanged (existing tests keep passing).
  - **`show`.** Lists `a.py#r1 — fresh (<hash12>)` for a region source.
  - **`who`.** After committing the verified region claim, `claimlock who c` names the committer for the `a.py#r1` line. A second region `r2` of the same file verified in a later commit by a different `user.email` is attributed to that email: each `hash:` line is unique, although both entries share the same `blob:`.
  - **`resolve`.** Two clones re-verify region `r1` of the same file to different region text (conflict on `hash`, `blob` and `pins:`). With `git checkout --theirs a.py`, `resolve` → `KEPT` and `check` exit 0. With new content in the region → `OWED`. A claim citing `a.py` whole and `a.py#r1` resolves by key, not collapsing the two entries.
- [ ] **Step 2: Run** them — expect failures.
- [ ] **Step 3: Implement.**
  - `gitio.index_blob(root, rel)` via `git --literal-pathspecs ls-files -s -- <rel>` (stage 0 line).
  - `gitio.verifier` takes `field`.
  - In `claims.freshness` (or an `Anchors` method), for a region source whose `(path, blob)` is not anchored, read `index_blob` → `cat_blob` → `regions.extract` → `region_hash`, and treat equality with `hash` as anchored.
  - `cli._verified` passes `("hash", s.hash)` for a region source, `("blob", s.blob)` otherwise.
  - `cmd_show` / `cmd_diff` index by key.
  - `merge`: `_pins` returns `Source` objects; compare sides by sorted keys; "whole" compares each key's current pin (`hasher.region` with `use_cache=False` for regions, `blob` for files) to the side's `pin`; written sources keep `region`/`blob`/`hash`; the owed path picks per key, taking `blob` and `hash` together from the matching side.
- [ ] **Step 4: Run** focused then full suite.
- [ ] **Step 5: Commit** `feat(regions): anchoring fallback, region diff/show/who, key-aware resolve`.

### Task 3: Rename detection, `renamed` state, `claimlock follow`

**Files:**
- Modify: `lib/claimlock/gitio.py` (`find_renames`), `lib/claimlock/claims.py` (`NON_FRESH`, severity, `renames_for`, `freshness(renames=)`, `Result.renames`, `evaluate`), `lib/claimlock/ops.py` (`follow`), `lib/claimlock/cli.py` (hints, check/stale/json/diff/show output, `_owed_states`, `follow` command), `README.md` (one `claimlock follow` row in the Commands table only)
- Create: `tests/test_renames.py`

**Interfaces:** Consumes Tasks 1–2. Produces `gitio.find_renames`, `claims.renames_for`, `Result.renames`, `ops.follow`, CLI `follow`.

Behaviour: spec §3.

- [ ] **Step 1: Write failing tests** in `tests/test_renames.py` (git):
  - **`find_renames` unit, against real repos:**
    - committed pure `git mv a.py b.py` → `{"a.py": ("b.py", <7-char sha of the mv commit>)}`
    - rename plus edit (≥50% similar) is found
    - chain `a → b → c` in two commits → `("c.py", <sha of the first deleting commit>)`
    - staged uncommitted `git mv` → `("b.py", "uncommitted")`
    - plain unstaged `mv` → `{}`
    - a deleted (not renamed) file → `{}`
    - outside git → `{}`
  - **`check`.** Verified claim on `a.py`, committed; `git mv a.py lib/a.py`, commit.
    - `check` exits 1 and prints `RENAMED  c`, then `         a.py: renamed → lib/a.py (<sha7>)`, then the hint line `         a source was renamed — run: claimlock follow c`.
    - the summary ends with `, 0 missing, 1 renamed`.
    - `check --json` source entry has `"state": "renamed", "renamed_to": "lib/a.py"`.
    - `stale` lists `c\t<area>\trenamed\ta.py`.
    - `diff c` prints `--- a.py: renamed to lib/a.py in <sha7> — run: claimlock follow c`.
  - **`follow` on the pure move:**
    - prints `followed c: a.py → lib/a.py (fresh)` and exits 0
    - the claim now cites `lib/a.py` with the same `blob`, and a recomputed `pins:` digest
    - `check` exits 0
  - **`follow` after a rename with an edit** → `(stale)`, exit 0.
  - **Region source** `a.py#r1` renamed → `followed c: a.py#r1 → lib/a.py#r1 (fresh)`, keeping `region` and `hash`.
  - **Digest handling.** A claim whose `pins:` digest did not match before `follow` keeps that digest line unchanged (still invalid). A claim without a digest gets none.
  - **Refusals, exit 1, file unchanged:**
    - no renamed sources → `c: no renamed sources`
    - new path already cited → `c: lib/a.py is already cited`
    - conflicted claim
    - outside git → `follow needs a git repository`
    - unknown id → `no claim 'x'`
  - **Owed claim.** An owed claim with a renamed source is listed by `check` as `OWED … (renamed)` and does not fail the gate; `follow` works on it.
  - **Hooks.** SessionStart context includes `1 renamed` after the committed rename. PostToolUse with HEAD unchanged still spawns no git (reuse the existing no-git assertion pattern in `tests/test_hooks.py`).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** per spec §3.
  - `find_renames` uses `-z` output parsing and paths relative to the root (`git diff --relative`), and returns only new paths that exist and are inside the root.
  - `claims.renames_for(project, sources)`: collects safe source paths that do not exist (`os.path.lexists` false); returns `{}` when there are none or the root is not in git; otherwise `gitio.find_renames`.
  - `evaluate` computes it once for all verified-claim sources and passes it to `freshness`; each `Result.renames` holds the entries for that claim's paths.
  - `cli._owed_states`, `cmd_diff`, `cmd_show` compute it for their sources.
  - `HINT["renamed"]`.
  - `_print_failing` prints `{key}: renamed → {new} ({sha})` for renamed sources.
  - JSON gains `renamed_to`.
  - `ops.follow` per §3.3.
  - CLI `follow` takes `ids` (nargs +), prints one line per followed source, exit 1 if any claim refused.
  - Add the README Commands row: `| \`claimlock follow\` | Rewrite the path of each renamed source (reported by \`check\` as \`renamed\`) to its new path, keeping its pins; the claim then reads fresh if the content is unchanged. |`
- [ ] **Step 4: Run** focused then full suite (existing summary-string assertions stay valid because `renamed` is appended last; update any test that asserts an exact full summary or the SessionStart counts sentence).
- [ ] **Step 5: Commit** `feat(renames): detect renamed sources, renamed state, claimlock follow`.

### Task 4: Self-test probes and documentation

**Files:**
- Modify: `lib/claimlock/selftest.py`, `tests/test_refs_import_selftest.py` (if it asserts the check count), `README.md`, `docs/format.md`, `skills/using-claimlock/SKILL.md`, `skills/operating-claimlock/SKILL.md`

**Interfaces:** Consumes Tasks 1–3.

- [ ] **Step 1: Self-test (spec §4).**
  - Plain arm: a second probe claim `probe-region`, citing `src.txt` region `probe`, in a file that has content outside the region. Expect:
    - `fresh` after verify
    - `fresh` after an edit outside the region
    - `stale` after an edit inside it
    - `missing` after deleting the end marker
  - Git arm, after its existing checks: a third probe `probe-rename` on `moved.txt`. Commit it; `git mv moved.txt moved2.txt`; commit. Expect `renamed`, then `ops.follow` → `fresh`.
  - `claimlock self-test` must print all checks passing; update any test asserting the check count.
- [ ] **Step 2: README.**
  - Statuses-and-states table: add a `renamed` row — "A source was moved (committed or staged); `check` names the new path. Run `claimlock follow <id>`."
  - A new `## Regions` section after "Commands" (add to the Contents list), covering:
    - marker syntax, with a short example in two comment styles
    - that the region is the lines strictly between the markers
    - the claim entry (`region`, `blob`, `hash`)
    - when to prefer a region: a large or shared file where the enforcing code is a small part
    - what fails: a missing, duplicated or unterminated marker reads `missing`
  - "How staleness works": one paragraph on region hashing and the anchoring fallback.
  - Limits: marker drift (code moved out of a region stays fresh); an unstaged plain `mv` is not detected (stage it); clean filters don't apply to regions; a file that isn't UTF-8 can't have regions.
  - The `unanchored` / team sections stay consistent.
- [ ] **Step 3: `docs/format.md`.**
  - Source entry keys `region` and `hash` with rules, and the new problem messages verbatim (spec §2.3).
  - The key notation.
  - The region extraction rules and reasons (§2.1–§2.2).
  - The `renamed` state with its severity, hint and output lines (§3.2).
  - A `claimlock follow` section (§3.3).
  - `diff`/`show`/`who` changes (§2.6).
  - The digest entry forms (§2.5).
  - The `resolve` table note on keys.
- [ ] **Step 4: Skills.**
  - `using-claimlock`: in the section on writing `sources`, prefer a region when the enforcing code is a small part of a large or shared file (marker example in a code span); a `renamed` claim → `claimlock follow <id>`, then re-read the claim if `follow` reports `stale`; a Red-flag row "The file was only moved; I'll just edit the path" → "Run `claimlock follow`: it keeps the pins honestly and updates the digest."
  - `operating-claimlock`: one line in the relevant section on `renamed` / `follow`.
  - Keep both skills' frontmatter and generic-content rules (`tests/test_skills_format.py`).
- [ ] **Step 5:** Full suite plus `python3 bin/claimlock self-test`. Commit `docs+selftest: regions and renames`.
