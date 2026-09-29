"""Whether a `kind: test` evidence ref still names a real test.

A claim's `evidence` cites its proof as a free-form string; nothing about a
claim's sources changes when a cited test is renamed or deleted, so a
`verified` claim can go on citing a test that no longer exists forever. This
module resolves that one thing and nothing else (design §3.2):

- Only `kind: test` refs are ever resolved. `measurement`, `source` and `run`
  refs are prose by design and are never parsed, never reported — the real
  store's one `run` ref reads "grep for prost/prost_types under crates/…/src",
  which is not a test name and has nothing to resolve it against.
- Nothing here executes anything by default. It reads files and looks for an
  identifier token; a claim file arrives by `git pull` and is untrusted input.
  `audit(..., ask_runners=True)` is the one exception and it is opt-in from
  `evidence --ask-runners` alone: it asks a real test runner what tests exist
  (`runners`), which is what lets an explicit ref be `resolved` rather than
  merely `matched`. Nothing from a ref reaches a command line even then — see
  that module's header for why.
"""
import re
from dataclasses import dataclass

from . import refs as R
from . import runners
from .project import safe_source

LOCATOR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIN_LOCATOR = 8

# A locator that appears in more files than this identifies nothing. Measured on
# a 140-claim store: a genuine test name appears in 1 file (its definition) or 2
# (definition plus one citation); the lowest common-word locator measured is
# `attributes` at 79 files, and `platform` at 1,001. Nothing real sits between 3
# and 79, so this threshold separates two populations rather than splitting one.
AMBIGUOUS_FILES = 3

# `evidence_globs` defaults to "**/*", not `refs`'s "**/*.md" — so, unlike
# `refs`, a real candidate here can be a tracked fixture, PDF or model blob
# far larger than any source file, and `audit` reads a whole candidate into
# memory just to look for one identifier token. Measured: a single 400 MB
# file peaked at 784 MB RSS and took 1.44s to scan. A few MB is ample for any
# source file that could plausibly contain a test name, so anything larger is
# skipped rather than read (and counted separately — see `audit`'s `skipped`
# return value — so a skip is visible, not a silent miss).
MAX_SCAN_BYTES = 4 * 1024 * 1024  # 4 MiB


def parse_ref(ref):
    """`(rel_file, test_name)` for an explicit `<repo-relative file>::<test>`
    ref, else None.

    A ref qualifies only when the part before the FIRST `::` looks like a path
    with a directory and a file extension. That is what keeps
    `pkg::mod::a_test` — the existing Rust-ish shape — falling through to the
    locator rule, and what refuses a bare `shell.test.ts::…`: two packages may
    hold that filename, so it identifies nothing (spec §4.2)."""
    head, sep, tail = ref.partition("::")
    if not sep or not tail.strip():
        return None
    head = head.strip()
    if "/" not in head or "." not in head.rsplit("/", 1)[1]:
        return None
    if head.startswith("/") or ".." in head.split("/"):
        return None
    return head, tail.strip()


def locator(ref):
    """The longest identifier-shaped token (>= MIN_LOCATOR chars) found in
    `ref`, or None when it has none — a citation claimlock cannot check,
    which is a different fact from one that is wrong. Ties keep whichever
    `max` meets first, which is deterministic for a given `ref`; no further
    tie-breaking is needed since nothing depends on which of two equal-length
    tokens wins."""
    return max((t for t in LOCATOR_RE.findall(ref) if len(t) >= MIN_LOCATOR),
               key=len, default=None)


def _appears_as_whole(leaf, text):
    """Whether `leaf` appears in `text` bounded on both sides by something
    other than an identifier character (`[A-Za-z0-9_]`) — the same
    discipline the prose locator rule gets for free from
    `LOCATOR_RE.findall` (which never returns a partial identifier), applied
    here by hand because an explicit ref's leaf is not always a single
    identifier token: a vitest name (`a listed case`) contains spaces, so a
    plain `in` check is the right shape but needs the same boundary a bare
    substring check lacks. Without it, `a_named_test` reads MATCHED against a
    file containing only `a_named_test_function` — a rename that leaves a
    superstring must not silently keep matching."""
    if not leaf:
        return False
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(leaf) + r"(?![A-Za-z0-9_])", text) is not None


@dataclass
class Check:
    claim_id: str
    ref: str
    locator: str | None
    outcome: str  # "resolved" | "matched" | "unresolved" | "unlocatable"
    reason: str | None = None  # "ambiguous" | "no-locator" | None


def _cache_key(root, rel, fn):
    """The unit `answers` should cache `fn`'s result under for `rel`.

    `vitest_tests` answers for the whole PACKAGE it lists, not just `rel` —
    measured at 445 tests across 43 files from one call — so caching it per
    file spawns one subprocess per cited file in that package instead of one
    per package. Keyed on the resolved package root (falling back to `rel`
    when none resolves, which costs nothing extra: `fn` would find the same
    absence and return None either way).

    `cargo_tests` answers per file INSTEAD: `--list` reports no file, so it
    re-labels every name it returns with the file it was asked about. A
    package-keyed cache would silently carry file A's relabelling onto file
    B's answer, so cargo stays keyed on `rel`."""
    if fn is runners.vitest_tests:
        pkg = runners.vitest_package_root(root, rel)
        return ("vitest", pkg) if pkg is not None else ("vitest-file", rel)
    return ("file", rel)


def _runner_listing(root, rel, answers):
    """What a runner lists for `rel`, or None when no runner owns its suffix or
    the one that does cannot be consulted. Consulted at most once per unit
    `_cache_key` names (a vitest package, or a cargo file): `answers` caches
    by that key, so a store citing many tests across many files in one vitest
    package spawns one subprocess, not one per file. Nothing is cached
    between runs — claimlock keeps no index."""
    fn = runners.for_file(rel)
    if fn is None:
        return None
    key = _cache_key(root, rel, fn)
    if key not in answers:
        answers[key] = fn(root, rel)
    return answers[key]


def _runner_lists(rel, name, listed):
    """Whether `listed` (a runner's `{(file, name)}`) holds `name` for `rel`.

    Exact first. Failing that, the ref may name only the LEAF of a runner's
    path — `attributes a frame` for vitest's `createShell > attributes a frame`,
    `a_case` for cargo's `codec_tests::a_case` — which is how a person cites a
    test and is still the runner saying that test exists in that file. The
    separator is required, so `case` does not resolve against `a listed case`."""
    if (rel, name) in listed:
        return True
    return any(f == rel and any(full.endswith(sep + name) for sep in (" > ", "::"))
               for f, full in listed)


def audit(project, claims, globs=None, ask_runners=False):
    """([Check], files_scanned, files_skipped) — one Check per `kind: test`
    evidence entry across `claims`; `measurement`/`source`/`run` entries are
    skipped and produce no Check at all. Walks `refs.files(project, None,
    globs)` exactly once (it already skips hidden directories, node_modules,
    the claims directory, gitignored files and anything that fails to decode
    as UTF-8), recording every scanned file's identifier tokens that are also
    a locator some claim is waiting on. `globs=None` defaults to
    `project.evidence_globs` (never to `project.marker_globs` — that fallback
    belongs to `refs.files` alone, for `refs.scan`).

    A candidate bigger than `MAX_SCAN_BYTES` is skipped rather than read —
    `files_skipped` counts these separately from `files_scanned` so a scan
    that missed its answer inside an oversized file is visible, not silent.
    This guard is local to `evidence`: `refs`'s own scan (bounded to
    `**/*.md` by default) is untouched by it.

    `ask_runners=True` consults a test runner for each EXPLICIT ref's file
    (`runners.for_file`, once per file) and lets its answer decide: listed →
    `resolved`, ran and did not list it → `unresolved`, could not be consulted →
    the static outcome stands, because an absent toolchain is not a false claim.
    A prose ref never reaches a runner — it names no file, so there is nothing
    to consult it about — and so can never be `matched` or runner-`resolved`."""
    globs = project.evidence_globs if globs is None else globs
    explicit, wanted = [], []
    for c in claims:
        for e in c.evidence:
            if not isinstance(e, dict) or e.get("kind") != "test":
                continue
            ref = e.get("ref")
            ref = ref if isinstance(ref, str) else ""
            parsed = parse_ref(ref)
            if parsed is not None:
                explicit.append((c.id, ref, parsed))
            else:
                wanted.append((c.id, ref, locator(ref)))

    needed = {loc for _, _, loc in wanted if loc is not None}
    counts = {}
    scanned = 0
    skipped = 0
    for p, _rel in R.files(project, None, globs):
        try:
            if p.stat().st_size > MAX_SCAN_BYTES:
                skipped += 1
                continue
            text = p.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        if not needed:
            continue
        found = set()
        for tok in LOCATOR_RE.findall(text):
            if len(tok) >= MIN_LOCATOR and tok in needed:
                found.add(tok)
        for tok in found:                      # once per FILE, not per occurrence
            counts[tok] = counts.get(tok, 0) + 1

    checks = []
    answers = {}                  # cache key -> runner listing; see _cache_key
    for cid, ref, (rel, name) in explicit:
        p = safe_source(project.root, rel)
        loc = f"{rel}::{name}"
        if p is None or not p.is_file():
            # Nothing to consult a runner about: no file, and the package-root
            # walk would start from a directory that may not exist either.
            checks.append(Check(cid, ref, loc, "unresolved"))
            continue
        try:
            text = p.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            checks.append(Check(cid, ref, loc, "unlocatable", "unreadable"))
            continue
        last = name.rsplit("::", 1)[-1].rsplit(" > ", 1)[-1].strip()
        outcome = "matched" if _appears_as_whole(last, text) else "unresolved"
        if ask_runners:
            listed = _runner_listing(project.root, rel, answers)
            if listed is not None:
                # The runner answered, so its answer is the answer — in both
                # directions. It can promote a name the static scan could not
                # see (one built in a loop) and demote one that is written in
                # the file but is not a test it would run.
                outcome = "resolved" if _runner_lists(rel, name, listed) else "unresolved"
        checks.append(Check(cid, ref, loc, outcome))
    for cid, ref, loc in wanted:
        reason = None
        if loc is None:
            outcome, reason = "unlocatable", "no-locator"
        else:
            n = counts.get(loc, 0)
            if n == 0:
                outcome = "unresolved"
            elif n > AMBIGUOUS_FILES:
                outcome, reason = "unlocatable", "ambiguous"
            else:
                outcome = "resolved"
        checks.append(Check(cid, ref, loc, outcome, reason))
    return checks, scanned, skipped


def exit_code(checks):
    """1 when any ref is `unresolved`, else 0. `matched` and `unlocatable` are
    reports, not failures — a store adopting this must not meet a cliff, the
    same reasoning that keeps orphans non-failing."""
    return 1 if any(c.outcome == "unresolved" for c in checks) else 0
