"""Claims: loading, validation and freshness.

Validation (`problems`) and freshness are separate on purpose. A claim can be
well-formed and stale, or malformed and fresh; reporting only one would hide
the other.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter
from .pins import Hasher
from .project import safe_source

STATUSES = ("verified", "unverified", "refuted")
KINDS = ("test", "measurement", "source", "run")
FIELDS = ("id", "area", "status", "verified_at", "evidence", "sources")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
BLOB_RE = re.compile(r"^[0-9a-f]{40}$")
NON_FRESH = ("unpinned", "stale", "missing")
_SEVERITY = {"fresh": 0, "unpinned": 1, "stale": 2, "missing": 3}


class StoreMissing(Exception):
    def __init__(self, path):
        super().__init__(f"no claims directory at {path} — run `claimlock init`")
        self.path = path


@dataclass
class Source:
    path: str
    blob: str | None


@dataclass
class Claim:
    path: Path
    text: str
    meta: dict
    body: str
    parse_error: str | None = None

    def _str(self, key):
        v = self.meta.get(key)
        return v if isinstance(v, str) and v else None

    @property
    def id(self):
        return self._str("id") or self.path.stem

    @property
    def area(self):
        return self._str("area") or "unfiled"

    @property
    def status(self):
        return self._str("status") or "unverified"

    @property
    def verified_at(self):
        return self._str("verified_at")

    @property
    def evidence(self):
        v = self.meta.get("evidence")
        return v if isinstance(v, list) else []

    @property
    def sources(self):
        raw = self.meta.get("sources")
        out = []
        for e in raw if isinstance(raw, list) else []:
            if isinstance(e, str):
                out.append(Source(e, None))
            elif isinstance(e, dict) and isinstance(e.get("path"), str):
                blob = e.get("blob")
                out.append(Source(e["path"], blob if isinstance(blob, str) else None))
        return out

    def headline(self):
        for line in self.body.splitlines():
            if line.strip():
                return line.strip()
        return ""


@dataclass
class Result:
    claim: Claim
    problems: list
    state: str | None
    per_source: list


def load_claims(project):
    if not project.claims_dir.is_dir():
        raise StoreMissing(project.claims_dir)
    out = []
    for p in sorted(project.claims_dir.glob("*.md")):
        if p.name == "README.md":
            continue
        try:
            # Path.read_text() applies universal-newline translation, which
            # silently turns "\r\n" into "\n" before we ever see the "\r" —
            # decode raw bytes instead so CRLF stays visible to the check below.
            text = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            out.append(Claim(p, "", {}, "", f"{p.name}:1: not valid UTF-8"))
            continue
        try:
            if "\r" in text:
                raise frontmatter.FrontmatterError(p.name, 1, "CR line endings are not supported; convert to LF")
            fm, body = frontmatter.split(text, p.name)
            meta = frontmatter.parse(fm, p.name)
        except frontmatter.FrontmatterError as e:
            out.append(Claim(p, text, {}, "", str(e)))
            continue
        out.append(Claim(p, text, meta, body.strip()))
    return out


def problems(claim, project, *, as_status=None):
    """Every reason this claim cannot be trusted as written."""
    if claim.parse_error:
        return [claim.parse_error]
    m, out = claim.meta, []
    for k in m:
        if k not in FIELDS:
            out.append(f"unknown field {k!r} (allowed: {', '.join(FIELDS)})")
    cid = m.get("id")
    if not isinstance(cid, str) or not cid:
        out.append("missing 'id'")
    else:
        if not ID_RE.match(cid):
            out.append(f"id {cid!r} is not kebab-case ([a-z0-9][a-z0-9-]*)")
        if cid != claim.path.stem:
            out.append(f"id {cid!r} does not match filename {claim.path.name!r}")
    for k in ("area", "verified_at"):
        if m.get(k) is not None and not isinstance(m[k], str):
            out.append(f"'{k}' must be a single value")
    status = as_status or m.get("status")
    if status is not None and (not isinstance(status, str) or status not in STATUSES):
        out.append(f"status {status!r} is not one of {', '.join(STATUSES)}")
    if not claim.body:
        out.append("no claim text after the frontmatter")

    ev = m.get("evidence")
    if ev is not None and not isinstance(ev, list):
        out.append("'evidence' must be a list")
    for e in ev if isinstance(ev, list) else []:
        if not (isinstance(e, dict) and isinstance(e.get("kind"), str)
                and isinstance(e.get("ref"), str) and e["ref"]):
            out.append(f"evidence entry needs 'kind' and 'ref': {e!r}")
        elif e["kind"] not in KINDS:
            out.append(f"evidence kind {e['kind']!r} is not one of {', '.join(KINDS)}")
        elif set(e) - {"kind", "ref"}:
            out.append(f"evidence entry has unknown keys {sorted(set(e) - {'kind', 'ref'})}")

    src = m.get("sources")
    if src is not None and not isinstance(src, list):
        out.append("'sources' must be a list")
    seen = set()
    for e in src if isinstance(src, list) else []:
        if isinstance(e, str):
            path = e
        elif isinstance(e, dict) and isinstance(e.get("path"), str):
            path = e["path"]
            extra = set(e) - {"path", "blob"}
            if extra:
                out.append(f"source {path!r} has unknown keys {sorted(extra)}")
            blob = e.get("blob")
            if blob is not None and not (isinstance(blob, str) and BLOB_RE.match(blob)):
                out.append(f"source {path!r} has a malformed blob (expected 40 lowercase hex)")
        else:
            out.append(f"source entry needs a 'path': {e!r}")
            continue
        if safe_source(project.root, path) is None:
            out.append(f"source path {path!r} must be relative and stay inside the project root")
        if path in seen:
            out.append(f"source {path!r} is listed twice")
        seen.add(path)

    if status == "verified":
        if not claim.evidence:
            out.append("status is 'verified' but no evidence is cited")
        if not claim.sources:
            out.append("status is 'verified' but no sources are listed, so it can never go stale")
    return out


def freshness(claim, project, hasher):
    """(state, [(path, state)]) for a verified claim; (None, []) otherwise.

    Worst source wins: missing > stale > unpinned > fresh.
    """
    if claim.parse_error or claim.status != "verified":
        return None, []
    per = []
    for s in claim.sources:
        if safe_source(project.root, s.path) is None:
            continue  # reported by problems(); never hash outside the root
        cur = hasher.blob(s.path)
        if cur is None:
            st = "missing"
        elif not s.blob:
            st = "unpinned"
        elif cur != s.blob:
            st = "stale"
        else:
            st = "fresh"
        per.append((s.path, st))
    state = max((st for _, st in per), key=_SEVERITY.__getitem__, default="fresh")
    return state, per


def open_hasher(project):
    return Hasher(project.root, project.state_dir / "cache" / "stat.json")


def evaluate(project, hasher):
    return [Result(c, problems(c, project), *freshness(c, project, hasher))
            for c in load_claims(project)]
