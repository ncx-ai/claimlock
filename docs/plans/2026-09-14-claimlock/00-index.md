# claimlock Implementation Plan — index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `claimlock`, a Claude Code plugin that pins written claims about a codebase to the content hashes of the files that could falsify them, reports drift loudly via a CLI gate and three non-blocking hooks, and ships four skills for using and operating it.

**Architecture:** A stdlib-only Python package under `lib/claimlock/` behind a thin `bin/claimlock` launcher (the plugin's `bin/` is on the Bash tool's PATH). Staleness = the file's git-blob SHA (computed in Python) differs from the pin written at `verify`. Hooks are subcommands of the same CLI (`claimlock hook <event>`) and always exit 0.

**Tech Stack:** Python ≥ 3.11 standard library only (`tomllib`, `hashlib`, `difflib`, `unittest`); git optional; Claude Code plugin manifest + hooks.

**Spec:** `docs/specs/2026-09-14-claimlock-design.md` (read it before any task).

## Global Constraints

- Python **≥ 3.11**, **standard library only** — no PyYAML, no pip installs, anywhere (runtime or tests).
- Tests: `python3 -m unittest discover -s tests -v` from the repo root. Every test in a fresh temp directory.
- The core **never requires git**; git only enhances `diff` and powers HEAD-movement detection.
- Exit codes: **0** clean, **1** findings, **2** store unreadable (bad config, missing claims dir). `hook` subcommands **always exit 0**.
- No hook ever exits 2 or sets `decision`. Stop warns via `systemMessage` only. SessionStart and PostToolUse use `hookSpecificOutput.additionalContext`.
- A hook in a project with no `.claimlock.toml` and no `claims/` dir prints nothing.
- Output strings from hooks ≤ 2,000 characters.
- Blob hash: `sha1(b"blob %d\0" % len(data) + data)` — equal to `git hash-object --no-filters`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv
  ```
- Skills and all repo content are **generic**: no references to the origin project, its files, or its platform.

## Spec amendments (made during planning — Task 2 applies them to the spec)

1. **Layout:** one launcher `bin/claimlock` + package `lib/claimlock/` (was "one file"). Focused modules are easier to review and test; the plugin still exposes one command.
2. **A claim whose frontmatter cannot be parsed is `invalid` (exit 1, message names file and line)**, not exit 2. One broken file must not hide the other claims' state. Exit 2 is reserved for bad config / missing claims dir.
3. **Stop baseline is replaced by the current survey** after each Stop (not unioned), so a problem that is fixed and then re-introduced warns again.
4. **`verify` also sets `status: verified`** (refuses `refuted` claims; un-refuting is a manual edit). This makes the workflow `new` → edit → `verify`.
5. **Stat cache never stores an entry whose mtime is < 2 s old** (racy-timestamp guard, as git does), so a same-size edit within the clock tick cannot be missed.
6. **Dangling-marker identity is `path:id`** (not line), so moving a marker does not re-warn.
7. **`claims_dir/README.md` is ignored** by the loader.

## File map

| File | Responsibility |
|---|---|
| `bin/claimlock` | Launcher: version check, put `lib/` on `sys.path`, call `claimlock.cli.main` |
| `lib/claimlock/__init__.py` | `VERSION` |
| `lib/claimlock/frontmatter.py` | Strict YAML-subset parse + surgical rewrite |
| `lib/claimlock/project.py` | Root discovery, `.claimlock.toml`, safe path resolution |
| `lib/claimlock/pins.py` | Blob hashing + stat cache |
| `lib/claimlock/claims.py` | Claim loading, validation (`problems`), freshness, `evaluate` |
| `lib/claimlock/gitio.py` | Every git subprocess call; all return `None` on failure |
| `lib/claimlock/snapshots.py` | Prior-content lookup (git object, else `.claimlock/objects/`) |
| `lib/claimlock/ops.py` | Mutations: `init_store`, `new_claim`, `verify` |
| `lib/claimlock/refs.py` | `Claim:` marker scanning |
| `lib/claimlock/importer.py` | Import the origin claim format |
| `lib/claimlock/selftest.py` | Detector self-test |
| `lib/claimlock/hooks.py` | SessionStart / Stop / PostToolUse |
| `lib/claimlock/cli.py` | argparse front end, output formatting |
| `hooks/hooks.json`, `.claude-plugin/{plugin,marketplace}.json` | Plugin wiring |
| `skills/*/SKILL.md` | Four skills |
| `tests/helpers.py`, `tests/test_*.py` | Test suite |

## Shared interfaces (every task's implementer must use these exact names)

```python
# frontmatter.py
class FrontmatterError(Exception): name: str; line: int; msg: str
def split(text: str, name: str) -> tuple[list[str], str]          # (frontmatter lines, body)
def parse(lines: list[str], name: str) -> dict                      # values: str | None | list[str | dict[str, str|None]]
def rewrite(text: str, name: str, *, status: str | None = None,
            verified_at: str | None = None, sources: list[dict] | None = None) -> str
def quote(s: str) -> str

# project.py
CONFIG = ".claimlock.toml"
class ConfigError(Exception)
@dataclass class Project: root: Path; claims_dir: Path; marker_globs: list[str]; marker_pattern: re.Pattern; has_config: bool
    state_dir: Path (property, root/".claimlock"); def has_store(self) -> bool
def load(start: Path) -> Project
def safe_source(root: Path, rel: str) -> Path | None
def is_within(p: Path, root: Path) -> bool

# pins.py
def blob_of_bytes(data: bytes) -> str
class Hasher: __init__(root: Path, cache_path: Path | None); blob(rel: str) -> str | None; hashed: int; save() -> None

# claims.py
class StoreMissing(Exception)
STATUSES, KINDS, ID_RE
@dataclass class Source: path: str; blob: str | None
@dataclass class Claim: path: Path; text: str; meta: dict; body: str; parse_error: str | None
    id, area, status, verified_at (properties); sources -> list[Source]; evidence -> list; headline() -> str
@dataclass class Result: claim: Claim; problems: list[str]; state: str | None; per_source: list[tuple[str, str]]
def load_claims(project) -> list[Claim]
def problems(claim, project, *, as_status: str | None = None) -> list[str]
def freshness(claim, project, hasher) -> tuple[str | None, list[tuple[str, str]]]
def evaluate(project, hasher) -> list[Result]
def open_hasher(project) -> Hasher
NON_FRESH = ("unpinned", "stale", "missing")

# gitio.py
def run(root, *args) -> subprocess.CompletedProcess | None
def in_git(root) -> bool; def head(root) -> str | None
def has_blob(root, sha) -> bool; def cat_blob(root, sha) -> bytes | None
def changed_paths(root, old: str | None, new: str) -> list[str]   # relative to root
def head_mark_paths(root) -> list[str]                             # absolute paths to stat

# snapshots.py
def store(project, blob: str, data: bytes) -> None
def load(project, blob: str) -> bytes | None
def prune(project, referenced: set[str]) -> None

# ops.py
class Refused(Exception)
def init_store(root: Path) -> list[str]
def new_claim(project, cid: str, area: str) -> Path
def verify(project, cid: str, now: str | None = None) -> list[tuple[str, str]]  # [(path, blob)]

# refs.py
@dataclass class Marker: path: str; line: int; id: str
def scan(project, only: set[str] | None = None) -> tuple[list[Marker], int]

# importer.py
def import_dir(project, src: Path) -> tuple[list[str], list[str]]   # (imported ids, error lines)

# selftest.py
def run(out=print) -> int

# hooks.py
def main(event: str, stdin_text: str, env: dict) -> int

# cli.py
def main(argv: list[str]) -> int
```

## Tasks

| # | File | Task |
|---|---|---|
| 1 | `01-hook-semantics-spike.md` | Verify hook/plugin behaviour in a real headless session; record findings |
| 2 | `02-scaffold-frontmatter.md` | Repo scaffold, launcher, test helpers, spec amendments, `frontmatter.py` |
| 3 | `03-project-pins.md` | `project.py`, `pins.py` |
| 4 | `04-claims.md` | `claims.py` |
| 5 | `05-cli-read.md` | `cli.py` with `init`, `new`, `check`, `stale`, `list`, `search`, `show`; `ops.init_store/new_claim` |
| 6 | `06-verify-diff.md` | `gitio.py`, `snapshots.py`, `ops.verify`, `verify` + `diff` commands |
| 7 | `07-refs-affected-import-selftest.md` | `refs.py`, `importer.py`, `selftest.py`, their commands |
| 8 | `08-hooks.md` | `hooks.py`, `hooks.json`, plugin manifests |
| 9 | `09-skills.md` | Four skills with RED/GREEN pressure checks |
| 10 | `10-docs-acceptance.md` | `README.md`, `docs/format.md`, `LICENSE`, end-to-end acceptance in a real session |

Tasks run in order; each ends with the full suite green and a commit.
