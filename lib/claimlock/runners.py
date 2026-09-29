"""Ask a test runner what tests exist — the only subprocess claimlock runs that
is not git (`gitio` owns those).

A static substring hit in a file says the name is written there; only the runner
says the name is a test it would run. `evidence --ask-runners` is opt-in for that
reason and for two costs: consulting cargo COMPILES the package, and consulting
vitest starts a vite server. `check` never reaches here.

Nothing from a claim reaches a command line. A ref contributes a repo-relative
PATH, which the caller has already put through `project.safe_source`, and a test
NAME, which is only ever compared in Python against what a runner printed.
Every argv below is a fixed list — no `shell=True`, no interpolation, and no
ref-derived argument at all.

A runner that cannot be consulted returns None, and the caller keeps its static
outcome: an absent toolchain, an unbuildable package or a monorepo with no
matching package root is not a false claim.
"""
import json
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .project import is_within

# Generous: a cold vitest start or a cargo build from scratch is slow, and
# timing out costs the answer (None -> the static outcome stands) rather than
# producing a wrong one. Not configurable — nothing here is on a request path.
TIMEOUT = 120

# The suffixes each runner owns. A file no runner claims is never handed to one:
# a Python or Go test would otherwise be "listed by vitest" — that is, absent
# from its answer — and every ref in it would read unresolved.
VITEST_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs", ".cjs")


def parse_vitest_json(raw, root):
    """`{(rel_file, name)}` from `vitest list --json`. `file` is absolute and
    `name` excludes the file, so both are normalised here rather than at the
    comparison, where a mistake would silently make every ref unresolved."""
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    out = set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        f, n = it.get("file"), it.get("name")
        if not isinstance(f, str) or not isinstance(n, str):
            continue
        try:
            out.add((Path(f).resolve().relative_to(Path(root).resolve()).as_posix(), n))
        except (ValueError, OSError):
            continue          # outside the repo — not ours to claim
    return out


def parse_cargo_list(out):
    """Test paths from `cargo test -- --list` (`a::b::c: test` lines).

    Measured shape of `cargo test --all-targets -- --list`: each target's names
    on stdout, then a blank line and an `N tests, M benchmarks` summary; the
    `Running unittests …` target headers go to stderr, so they are never seen
    here. The summary and blank lines are dropped twice over — they hold no
    `": "` at all, so `rpartition` leaves `sep` and `name` empty AND `kind` the
    whole line — while the `benches: benchmark` lines `--all-targets` adds are
    dropped by the `kind` test alone. Stated because the two are not
    interchangeable: removing the `kind` test lets benchmarks through and leaves
    the summary lines still correctly dropped, so a test that only shows a
    summary line being dropped does not cover it."""
    names = set()
    for line in out.splitlines():
        name, sep, kind = line.rpartition(": ")
        if sep and kind.strip() == "test" and name.strip():
            names.add(name.strip())
    return names


def _package_root(root, rel_file, manifest, contains=None):
    """The nearest ancestor directory of `rel_file`, at or below `root`, holding
    `manifest` (and, when `contains` is given, a manifest mentioning it), or
    None. The walk stops AT `root`: a package above the tree claimlock was
    pointed at is not this project's.

    The walk is over UNRESOLVED lexical parents, so a directory symlink inside
    the tree can point `d` somewhere that is lexically "at or below `root`"
    but resolves outside it (a double symlink: a tracked dir symlink into
    another tracked dir symlink pointing off-repo). `safe_source` already lets
    such a path through, because IT resolves inside the root — the escape is
    only in what `d` itself resolves to. So every candidate is
    containment-checked against the resolved root before it is returned,
    which is the one check `os.path`/`pathlib` never does for you."""
    root = Path(root)
    root_real = root.resolve()
    d = (root / rel_file).parent
    while True:
        m = d / manifest
        if m.is_file():
            ok = contains is None
            if not ok:
                try:
                    ok = contains in m.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    ok = False
            if ok and is_within(d.resolve(), root_real):
                return d
        if d == root or d.parent == d:
            return None
        d = d.parent


def vitest_package_root(root, rel_file):
    """The nearest ancestor directory of `rel_file` whose package.json mentions
    vitest, or None. A monorepo root usually has a package.json WITHOUT vitest,
    and listing from there lists the wrong project.

    "Mentions" is deliberately a substring of the whole file rather than a
    dependency-key lookup: a package may name vitest only in a `scripts` entry
    (`"test": "vitest run"`) and a key lookup would miss it, which costs the
    answer. The cost of the looser test is a false positive — a package merely
    whose NAME contains `vitest` — and there `npx` fails or lists nothing, which
    returns None and leaves the static outcome standing."""
    return _package_root(root, rel_file, "package.json", "vitest")


def cargo_package_root(root, rel_file):
    """The nearest ancestor directory of `rel_file` holding a Cargo.toml, or
    None. Nearest, so a workspace member is listed rather than the workspace."""
    return _package_root(root, rel_file, "Cargo.toml")


def vitest_tests(root, rel_file):
    """`{(rel_file, name)}` vitest lists for the package owning `rel_file`, or
    None when vitest cannot be consulted."""
    pkg = vitest_package_root(root, rel_file)
    if pkg is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        # NEVER a path from a ref, a claim or the repository: `--json <path>`
        # WRITES the report there. During this feature's investigation
        # `vitest list --json src/shell.test.ts` — meant as "list this file as
        # JSON" — was read as "write the report to that path" and truncated a
        # real 251-line test file. The report path is always one claimlock
        # created inside a temporary directory.
        out = Path(tmp) / "vitest-list.json"
        try:
            subprocess.run(["npx", "vitest", "list", "--json", str(out)],
                           cwd=pkg, capture_output=True, text=True,
                           timeout=TIMEOUT, check=False)
            return parse_vitest_json(out.read_text(encoding="utf-8"), root)
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeDecodeError):
            return None


def cargo_tests(root, rel_file):
    """`{(rel_file, name)}` cargo lists for the package owning `rel_file`, or
    None when cargo cannot be consulted.

    `--list` reports no file, so every name is paired with the file that was
    asked about. That is the honest limit of what cargo says: it confirms the
    package has a test of that name, not that the test is in that file."""
    pkg = cargo_package_root(root, rel_file)
    if pkg is None:
        return None
    try:
        r = subprocess.run(["cargo", "test", "--all-targets", "--", "--list"],
                           cwd=pkg, capture_output=True, text=True,
                           timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if r.returncode != 0:
        # A package that does not build lists nothing; reading that as "these
        # tests do not exist" would fail every ref in it.
        return None
    return {(rel_file, name) for name in parse_cargo_list(r.stdout or "")}


def for_file(rel_file):
    """The runner that can be asked about `rel_file`, or None when no runner
    owns its suffix. Resolved from this module's globals at call time, so a
    test can replace a runner without the dispatch outrunning it."""
    suffix = PurePosixPath(rel_file).suffix.lower()
    if suffix in VITEST_SUFFIXES:
        return vitest_tests
    if suffix == ".rs":
        return cargo_tests
    return None
