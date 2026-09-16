"""Whether a `kind: test` evidence ref still names a real test.

A claim's `evidence` cites its proof as a free-form string; nothing about a
claim's sources changes when a cited test is renamed or deleted, so a
`verified` claim can go on citing a test that no longer exists forever. This
module resolves that one thing and nothing else (design §3.2):

- Only `kind: test` refs are ever resolved. `measurement`, `source` and `run`
  refs are prose by design and are never parsed, never reported — the real
  store's one `run` ref reads "grep for prost/prost_types under crates/…/src",
  which is not a test name and has nothing to resolve it against.
- Nothing here executes anything. It reads files and looks for an identifier
  token; a claim file arrives by `git pull` and is untrusted input.
"""
import re
from dataclasses import dataclass

from . import refs as R

LOCATOR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIN_LOCATOR = 8

# `evidence_globs` defaults to "**/*", not `refs`'s "**/*.md" — so, unlike
# `refs`, a real candidate here can be a tracked fixture, PDF or model blob
# far larger than any source file, and `audit` reads a whole candidate into
# memory just to look for one identifier token. Measured: a single 400 MB
# file peaked at 784 MB RSS and took 1.44s to scan. A few MB is ample for any
# source file that could plausibly contain a test name, so anything larger is
# skipped rather than read (and counted separately — see `audit`'s `skipped`
# return value — so a skip is visible, not a silent miss).
MAX_SCAN_BYTES = 4 * 1024 * 1024  # 4 MiB


def locator(ref):
    """The longest identifier-shaped token (>= MIN_LOCATOR chars) found in
    `ref`, or None when it has none — a citation claimlock cannot check,
    which is a different fact from one that is wrong. Ties keep whichever
    `max` meets first, which is deterministic for a given `ref`; no further
    tie-breaking is needed since nothing depends on which of two equal-length
    tokens wins."""
    return max((t for t in LOCATOR_RE.findall(ref) if len(t) >= MIN_LOCATOR),
               key=len, default=None)


@dataclass
class Check:
    claim_id: str
    ref: str
    locator: str | None
    outcome: str  # "resolved" | "unresolved" | "unlocatable"


def audit(project, claims, globs=None):
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
    `**/*.md` by default) is untouched by it."""
    globs = project.evidence_globs if globs is None else globs
    wanted = []
    for c in claims:
        for e in c.evidence:
            if not isinstance(e, dict) or e.get("kind") != "test":
                continue
            ref = e.get("ref")
            ref = ref if isinstance(ref, str) else ""
            wanted.append((c.id, ref, locator(ref)))

    needed = {loc for _, _, loc in wanted if loc is not None}
    seen = set()
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
        for tok in LOCATOR_RE.findall(text):
            if len(tok) >= MIN_LOCATOR and tok in needed:
                seen.add(tok)

    checks = []
    for cid, ref, loc in wanted:
        if loc is None:
            outcome = "unlocatable"
        elif loc in seen:
            outcome = "resolved"
        else:
            outcome = "unresolved"
        checks.append(Check(cid, ref, loc, outcome))
    return checks, scanned, skipped
