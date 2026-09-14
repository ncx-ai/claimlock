# claimlock for teams — implementation plan index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make claimlock work for several developers sharing one repository: a scoped CI gate, committed hand-offs, anchored pins every clone can diff, line-ending-independent pins, git-derived verifier history, a merge helper, and team-aware hooks.

**Architecture:** Everything stays in the stdlib-only package `lib/claimlock/`. All new git access goes through `gitio.py`. Pins inside git are computed by one `git hash-object --stdin-paths` batch (git's normalization); outside git, raw blob SHA as today. Anchoring is one `git rev-list --objects --all -- <paths>` plus `git ls-files -s`. Conflict resolution lives in a new `merge.py`. Snapshots (`snapshots.py`) are deleted.

**Tech Stack:** Python ≥ 3.11 standard library, git ≥ 2.16 (`--find-object`, `check-ignore`), `unittest`.

**Spec:** `docs/specs/2026-09-14-claimlock-teams-design.md` (read it, including "Amendments (2026-09-14, planning)" T1–T5). Base spec: `docs/specs/2026-09-14-claimlock-design.md`.

## Global Constraints

- Python ≥ 3.11 stdlib only for `lib/`; the launcher `bin/claimlock` must keep parsing on Python 3.6+ (do not touch it unless a task says so).
- Tests: `python3 -m unittest discover -s tests -v` from the repo root, and `python3 -W error::ResourceWarning -m unittest discover -s tests` must also be clean. Git-dependent tests run (not skip) when git is installed; `chmod 000` tests skip only as root.
- Exit codes: 0 clean, 1 findings, 2 store unreadable / cannot run (bad config, missing or unreadable claims dir, `--changed` outside git or unresolvable base, `owe` with no identity). `hook` always exits 0, never prints on failure, never sets `decision`.
- A claim reads `fresh` only when every cited source's current content equals its pin. No change may create a false `fresh`.
- Every git subprocess lives in `gitio.py` and degrades to None / False / [] on failure.
- `owed` never fails any `check` (spec amendment T3).
- `verify` and `resolve` always re-hash with the stat cache bypassed (amendment T5).
- Hook PostToolUse with HEAD unchanged must not invoke git.
- Output from hooks ≤ 2,000 characters.
- No local absolute paths in tracked files (`tests/test_plugin_manifest.py` leak guard). No references to the origin project.
- The README must list every command in `claimlock --help` as `claimlock <cmd>` (`tests/test_plugin_manifest.py`): a task that adds a command adds its README row in the same task.
- Revert temporary mutations by re-applying the inverse edit, never `git checkout --`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv
  ```
- Test counts in task texts are deltas ("+N tests"); report the observed total — the suite was **122** at the start of this plan.

## File map

| File | Change |
|---|---|
| `lib/claimlock/gitio.py` | + `root_is_ignored`, `anchored_blobs`, `hash_paths`, `user_email`, `short_head`, `merge_base`, `changed_since`, `verifier`, `range_log`, `dirty_paths`, `commits_behind` |
| `lib/claimlock/refs.py` | ignored-root fallback to the walk |
| `lib/claimlock/claims.py` | `StoreUnreadable`; `owed` status + fields; `conflicted`; `unanchored`; anchors; hasher mode |
| `lib/claimlock/pins.py` | `Hasher(mode=)`, `prime()`, `blob(use_cache=)`; mode-tagged cache |
| `lib/claimlock/frontmatter.py` | `rewrite(..., set_fields=, remove=)` |
| `lib/claimlock/ops.py` | `verify` (no snapshots, strips `verified_at`/owed fields), `owe`, `NeedsIdentity` |
| `lib/claimlock/merge.py` | NEW: conflict hunk parsing + `resolve` |
| `lib/claimlock/snapshots.py` | DELETED |
| `lib/claimlock/selftest.py` | git arm stages the probe; `unanchored` check |
| `lib/claimlock/cli.py` | `check --changed`, `owe`, `resolve`, `who`, `show` verifier lines, `--owed-by/--mine`, diff from git only, new hints, CI snippet |
| `lib/claimlock/hooks.py` | owed-to-you, attribution, conflict notice, Stop uncommitted split, survey keys |
| `tests/…` | per task |
| `README.md`, `docs/format.md`, `skills/using-claimlock`, `skills/operating-claimlock`, `tests/skills/*` | Task 8 (plus README command rows in Tasks 5–6) |

## Shared interfaces (exact names)

```python
# gitio.py
def root_is_ignored(root) -> bool
def anchored_blobs(root, rels: list[str]) -> set[str] | None       # None outside git / git failure
def hash_paths(root, rels: list[str]) -> dict[str, str] | None     # rel -> normalized blob; None on failure
def user_email(root) -> str | None
def short_head(root) -> str | None                                  # 7-char
def merge_base(root, base: str) -> str | None
def changed_since(root, commit: str) -> list[str]                   # committed changes commit..HEAD, root-relative
def verifier(root, claim_rel: str, blob: str) -> tuple[str, str, str] | None   # (email, iso, short sha)
def range_log(root, old: str | None, new: str) -> list[tuple[str, str, str, list[str]]]  # (sha7, email, subject, paths), newest first
def dirty_paths(root) -> list[str]                                  # tracked paths differing from HEAD, root-relative
def commits_behind(root, commit: str) -> int | None

# claims.py
class StoreUnreadable(StoreMissing)
STATUSES = ("verified", "unverified", "refuted", "owed")
NON_FRESH = ("unpinned", "unanchored", "stale", "missing")
Claim(path, text, meta, body, parse_error=None, conflicted=False)   # + owed_by, owed_since properties
def anchors_for(project, paths) -> set[str] | None
def freshness(claim, project, hasher, anchors=None) -> (state|None, [(path, state)])
def evaluate(project, hasher) -> list[Result]                       # primes hasher, computes anchors once
def open_hasher(project) -> Hasher                                   # mode "git" inside a work tree, else "raw"

# pins.py
class Hasher: __init__(root, cache_path, mode="raw"); mode; prime(rels); blob(rel, use_cache=True); hashed; save()

# frontmatter.py
def rewrite(text, name, *, status=None, verified_at=None, sources=None, set_fields=None, remove=()) -> str

# ops.py
class NeedsIdentity(Exception)
def verify(project, cid) -> list[tuple[str, str]]
def owe(project, cid, to=None, reason=None, today=None) -> tuple[str, str]   # (owed_by, owed_since)

# merge.py
def resolve_claim(project, claim) -> tuple[str, str]   # ("kept"|"owed"|"left", message)
```

## Tasks

| # | File | Task | Model |
|---|---|---|---|
| 1 | `01-prerequisites.md` | Ignored-root marker scan fallback; unreadable claims dir → exit 2 | sonnet |
| 2 | `02-anchoring.md` | `unanchored` state, anchor set (history + index), remove snapshots, `diff` from git only, selftest | sonnet |
| 3 | `03-normalized-hashing.md` | Hasher git mode (`hash-object --stdin-paths`), mode-tagged cache, verify bypasses cache, CRLF clone test | sonnet |
| 4 | `04-owed-conflicted-model.md` | `owed` status + fields, `conflicted` problem, `verified_at` dropped, `rewrite(set_fields, remove)` | sonnet |
| 5 | `05-owe-resolve.md` | `claimlock owe`, `merge.py` + `claimlock resolve`, two-clone merge tests | opus |
| 6 | `06-team-gate-who.md` | `check --changed`, owed listing, `who`, `show` verifier lines, `--owed-by/--mine`, CI snippet | sonnet |
| 7 | `07-team-hooks.md` | Hooks: owed-to-you, attribution, conflict notice, Stop uncommitted split, no-git common path test | opus |
| 8 | `08-docs-skills.md` | README team section + migration, format.md, skills + one pressure scenario | opus |

Tasks run in order; each ends with the full suite green and a commit.
