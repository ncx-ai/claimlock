"""Claims: loading, validation and freshness.

Validation (`problems`) and freshness are separate on purpose. A claim can be
well-formed and stale, or malformed and fresh; reporting only one would hide
the other.
"""
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter, gitio, regions
from .pins import Hasher
from .project import is_within, safe_source

STATUSES = ("verified", "unverified", "refuted", "owed")
KINDS = ("test", "measurement", "source", "run")
FIELDS = ("id", "area", "status", "verified_at", "owed_by", "owed_since", "evidence", "sources", "pins")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
BLOB_RE = re.compile(r"^[0-9a-f]{40}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
SINCE_RE = re.compile(r"^([0-9a-f]{7,40}|none)$")
_CONFLICT_START = re.compile(r"^<{7} ", re.M)
_CONFLICT_END = re.compile(r"^>{7} ", re.M)
NON_FRESH = ("unpinned", "unanchored", "stale", "missing", "renamed")
_SEVERITY = {"fresh": 0, "unpinned": 1, "unanchored": 2, "stale": 3, "renamed": 4, "missing": 5}


def normalize_email(s):
    """The one identity comparison: `s` stripped and casefolded when it is an
    email address, else None. Every "is this owed to that person" check
    compares normalized values; the value written into `owed_by` keeps the
    case the person gave."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    return s.casefold() if EMAIL_RE.match(s) else None


def pin_digest(sources):
    """The digest of one verification's whole pin set, written as `pins:`.

    sha1 over the sorted entries of an iterable of `Source`: a whole-file
    source is `[path, blob or ""]` (unchanged from before regions, so every
    digest ever written stays valid), a region source is
    `[path, region, blob or "", hash or ""]`. Sorted, so reordering `sources`
    by hand keeps it. Because every `verify` rewrites this one line, two
    branches that re-verify a claim to different contents conflict on it even
    when they changed different sources — which is what stops a merge from
    producing a fresh-looking pin set no single verification covered."""
    entries = []
    for s in sources:
        if s.region is None:
            entries.append([s.path, s.blob or ""])
        else:
            entries.append([s.path, s.region, s.blob or "", s.hash or ""])
    data = json.dumps(sorted(entries))
    return hashlib.sha1(data.encode("ascii")).hexdigest()


class StoreMissing(Exception):
    def __init__(self, path):
        super().__init__(f"no claims directory at {path} — run `claimlock init`")
        self.path = path


class StoreUnreadable(StoreMissing):
    """The claims directory exists but cannot be listed. A subclass of
    StoreMissing so every caller that maps "no readable store" to exit 2
    handles it without change; reading it as an empty store would be a
    false clean."""

    def __init__(self, path, error):
        Exception.__init__(self, f"claims directory {path} cannot be read: "
                                 f"{getattr(error, 'strerror', None) or error}")
        self.path = path


@dataclass
class Source:
    path: str
    blob: str | None
    region: str | None = None
    hash: str | None = None

    @property
    def key(self) -> str:
        """`path`, or `path#region` for a region source — how every
        per-source listing (check, stale, show, diff, --json, hooks) names
        this source."""
        return self.path if self.region is None else f"{self.path}#{self.region}"

    @property
    def pin(self) -> str | None:
        """The pin this source is judged against: `hash` for a region
        source, `blob` for a whole file."""
        return self.hash if self.region is not None else self.blob


@dataclass
class Claim:
    path: Path
    text: str
    meta: dict
    body: str
    parse_error: str | None = None
    conflicted: bool = False

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
    def owed_by(self):
        return self._str("owed_by")

    @property
    def owed_since(self):
        return self._str("owed_since")

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
                region = e.get("region")
                h = e.get("hash")
                out.append(Source(e["path"],
                                   blob if isinstance(blob, str) else None,
                                   region if isinstance(region, str) else None,
                                   h if isinstance(h, str) else None))
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
    renames: dict = field(default_factory=dict)
    """{path: (new_path, sha7 | "uncommitted")} for this claim's renamed
    sources (spec §3.1); {} when none of its sources are renamed."""


def load_claims(project):
    if not project.claims_dir.is_dir():
        raise StoreMissing(project.claims_dir)
    out = []
    try:
        names = sorted(os.listdir(project.claims_dir))
    except OSError as e:
        raise StoreUnreadable(project.claims_dir, e) from None
    for name in names:
        if not name.endswith(".md") or name == "README.md":
            continue
        p = project.claims_dir / name
        try:
            # Path.read_text() applies universal-newline translation, which
            # silently turns "\r\n" into "\n" before we ever see the "\r" —
            # decode raw bytes instead so CRLF stays visible to the check below.
            text = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            out.append(Claim(p, "", {}, "", f"{p.name}:1: not valid UTF-8"))
            continue
        except OSError as e:
            # Reported as an invalid claim, never raised: one unreadable file
            # must not hide the state of every other claim.
            out.append(Claim(p, "", {}, "", f"{p.name}:1: cannot be read: {e.strerror or e}"))
            continue
        # A clone checked out with core.autocrlf=true delivers claim files as
        # CRLF; normalize to LF before anything else looks at the text (a
        # lone "\r" not part of a "\r\n" pair is left in place and still
        # rejected below — that is not a line-ending convention, it's a
        # malformed file).
        if "\r\n" in text:
            text = text.replace("\r\n", "\n")
        if _CONFLICT_START.search(text) and _CONFLICT_END.search(text):
            out.append(Claim(p, text, {}, "", None, conflicted=True))
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


def problems(claim, project, *, as_status=None, digest=True):
    """Every reason this claim cannot be trusted as written. `digest=False`
    skips the `pins:` digest check — for `verify`, which rewrites it."""
    if claim.conflicted:
        return [f"{claim.path.name}: contains git conflict markers — run `claimlock resolve`"]
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
    for k in ("area", "verified_at", "owed_by", "owed_since"):
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
        region = raw_region = None
        if isinstance(e, str):
            path = e
        elif isinstance(e, dict) and isinstance(e.get("path"), str):
            path = e["path"]
            extra = set(e) - {"path", "blob", "region", "hash"}
            if extra:
                out.append(f"source {path!r} has unknown keys {sorted(extra)}")
            blob = e.get("blob")
            if blob is not None and not (isinstance(blob, str) and BLOB_RE.match(blob)):
                out.append(f"source {path!r} has a malformed blob (expected 40 lowercase hex)")
            raw_region = e.get("region")
            if raw_region is not None:
                if not (isinstance(raw_region, str) and regions.NAME_RE.match(raw_region)):
                    out.append(f"source {path!r} has a malformed region name")
                else:
                    region = raw_region
            h = e.get("hash")
            if h is not None and not (isinstance(h, str) and BLOB_RE.match(h)):
                out.append(f"source {path!r} has a malformed hash")
            if h is not None and raw_region is None:
                out.append(f"source {path!r} has a hash but no region")
            if region is not None:
                has_blob = isinstance(blob, str) and bool(BLOB_RE.match(blob))
                has_hash = isinstance(h, str) and bool(BLOB_RE.match(h))
                if has_blob != has_hash:
                    out.append(f"source {path + '#' + region!r} must pin both blob and hash")
        else:
            out.append(f"source entry needs a 'path': {e!r}")
            continue
        if safe_source(project.root, path) is None:
            out.append(f"source path {path!r} must be relative and stay inside the project root")
        # Keyed by the raw region value, not the validated one: a malformed
        # region name must not fall back to the bare path and collide with a
        # genuine whole-file entry for the same path, while two identical
        # malformed entries are still duplicates of each other.
        dup_key = path if raw_region is None else f"{path}#{raw_region}"
        if dup_key in seen:
            out.append(f"source {dup_key!r} is listed twice")
        seen.add(dup_key)

    # A claim with no `pins:` line predates the digest and is accepted; its
    # next verify writes one.
    pins = m.get("pins")
    if pins is not None and digest:
        if not (isinstance(pins, str) and BLOB_RE.match(pins)):
            out.append("'pins' must be a pin-set digest (40 lowercase hex), as written by claimlock verify")
        elif pins != pin_digest(claim.sources):
            out.append("pins digest does not match the listed sources — they were edited by hand or "
                       "combined from different verifications; re-check the claim, then claimlock verify")

    if status == "owed":
        if not (isinstance(m.get("owed_by"), str) and EMAIL_RE.match(m["owed_by"])):
            out.append("status is 'owed' but 'owed_by' is not an email address")
        if not (isinstance(m.get("owed_since"), str) and SINCE_RE.match(m["owed_since"])):
            out.append("status is 'owed' but 'owed_since' is not a commit id (7-40 hex) or 'none'")
        if not claim.sources:
            out.append("status is 'owed' but no sources are listed")
    elif as_status is None:
        for k in ("owed_by", "owed_since"):
            if m.get(k) is not None:
                out.append(f"'{k}' is only valid with status: owed")
    if status == "verified":
        if not claim.evidence:
            out.append("status is 'verified' but no evidence is cited")
        if not claim.sources:
            out.append("status is 'verified' but no sources are listed, so it can never go stale")
    return out


@dataclass
class Anchors:
    """The blob ids every clone can recover for a set of sources (`blobs`),
    plus the source paths git itself refuses to track (`ignored`). A pin
    anchors when its blob is reachable/staged, or its own path is one git
    ignores — content that can never be committed or staged is not owed one."""
    blobs: set[str]
    ignored: set[str]

    def ok(self, path, blob) -> bool:
        return blob in self.blobs or path in self.ignored

    def ok_region(self, root, path, blob, region, region_hash) -> bool:
        """Anchored via `ok(path, blob)`, or via the fallback of spec §2.4: the
        file's currently staged content (index stage 0), read straight from
        git, still contains this region with the same hash. Only reached when
        the whole-file blob is itself unanchored, so the common path (an
        anchored blob) never runs a git call for this."""
        if self.ok(path, blob):
            return True
        return staged_region_blob(root, path, region, region_hash) is not None


def staged_region_blob(root, path, region, region_hash):
    """The blob id staged for `path` (index stage 0) when that staged content
    contains `region` with hash `region_hash`, else None (outside git, not
    staged, or the region differs or cannot be extracted there)."""
    sha = gitio.index_blob(root, path)
    if sha is None:
        return None
    data = gitio.cat_blob(root, sha)
    if data is None:
        return None
    try:
        text = regions.extract(data, region)
    except regions.RegionError:
        return None
    return sha if regions.region_hash(text) == region_hash else None


def source_dict(s):
    """A `frontmatter._sources_block` entry for Source `s`: `path`, plus each
    of `region`, `blob`, `hash` that is set (used by `ops` and `merge`)."""
    d = {"path": s.path}
    if s.region:
        d["region"] = s.region
    if s.blob:
        d["blob"] = s.blob
    if s.hash:
        d["hash"] = s.hash
    return d


def anchors_for(project, sources):
    """The Anchors for these sources (see gitio.anchored_blobs /
    gitio.ignored_paths), or None when anchoring is not evaluated at all:
    outside git, on git failure, or when the store root itself lies inside a
    directory an enclosing repository ignores — there, nothing under it can
    ever be committed or staged, so anchoring has no valid answer to give."""
    if gitio.root_is_ignored(project.root):
        return None
    valid = [s for s in sources if safe_source(project.root, s.path) is not None]
    cited = {s.path for s in valid}
    paths = sorted(cited | _symlink_targets(project.root, cited))
    if not paths:
        return Anchors(set(), set())
    blobs = gitio.anchored_blobs(project.root, paths, pins={s.blob for s in valid if s.blob})
    if blobs is None:
        return None
    remaining = sorted({s.path for s in valid if s.blob not in blobs})
    ignored = gitio.ignored_paths(project.root, remaining) if remaining else set()
    if ignored is None:
        ignored = set()
    return Anchors(blobs, ignored)


def _symlink_targets(root, rels):
    """Root-relative paths of the regular files that cited paths reach through
    a symlink — the file itself or any directory above it — when those files
    lie inside the root. A pin hashes the content found by following the
    links, while git stores a symlink as its link text, so no commit ever
    holds that content at the cited path (`link/a.py` under a linked `link/`
    has no blob in history): it can only be anchored at the resolved path.
    Plain `resolve`, no git; a path escaping the root is already refused by
    `safe_source`, and one that is dangling or names a non-file adds nothing."""
    base = Path(root).resolve()
    out = set()
    for rel in rels:
        try:
            target = (base / rel).resolve(strict=True)
            if not stat.S_ISREG(os.stat(target).st_mode):
                continue
        except (OSError, RuntimeError):
            continue
        if is_within(target, base):
            resolved = target.relative_to(base).as_posix()
            if resolved != Path(rel).as_posix():
                out.add(resolved)
    return out


def freshness(claim, project, hasher, anchors=None, as_status=None, renames=None):
    """(state, [(key, state)]) for a verified claim; (None, []) otherwise.

    Worst source wins: missing > renamed > stale > unanchored > unpinned >
    fresh. A region source (spec 2.4) is judged by its region hash, not its
    file's whole-content blob; anchoring for it checks `Anchors.ok` on the
    whole-file blob first, falling back (`Anchors.ok_region`) to the file's
    currently staged content still containing the same region hash — so
    uncommitted edits elsewhere in the file don't leave the pin unanchored
    forever. `as_status="verified"` evaluates a claim's pins as if it were verified —
    `diff` and `show` pass it for an `owed` claim, whose pins are kept
    exactly so the hand-off recipient can see what moved. `evaluate` never
    does: an owed claim has no freshness verdict in `check` or the hooks.

    `renames` ({path: (new_path, sha)}, from `renames_for`) reclassifies a
    missing source as `renamed` (spec §3.2) instead of `missing` when its
    path is a key of it — keyed by the bare path, so a whole-file and a
    region source of the same missing path share the same verdict.
    """
    if claim.parse_error or claim.conflicted or (as_status or claim.status) != "verified":
        return None, []
    per = []
    for s in claim.sources:
        if safe_source(project.root, s.path) is None:
            continue  # reported by problems(); never hash outside the root
        if s.region is not None:
            cur, _reason = hasher.region(s.path, s.region)
        else:
            cur = hasher.blob(s.path)
        pin = s.pin
        if cur is None:
            st = "renamed" if renames and s.path in renames else "missing"
        elif not pin:
            st = "unpinned"
        elif cur != pin:
            st = "stale"
        elif anchors is not None and not (
                anchors.ok_region(project.root, s.path, s.blob, s.region, s.hash)
                if s.region is not None else anchors.ok(s.path, s.blob)):
            st = "unanchored"
        else:
            st = "fresh"
        per.append((s.key, st))
    state = max((st for _, st in per), key=_SEVERITY.__getitem__, default="fresh")
    return state, per


def renames_for(project, sources):
    """{path: (new_path, sha7 | "uncommitted")} for the given sources' paths
    that are currently missing (`os.path.lexists` false), via
    `gitio.find_renames` (spec §3.1). {} when none of the paths are missing,
    or the project root is not a git work tree — the common path (nothing
    missing) never runs a git call."""
    missing = set()
    for s in sources:
        p = safe_source(project.root, s.path)
        if p is not None and not os.path.lexists(p):
            missing.add(s.path)
    if not missing or not gitio.in_git(project.root):
        return {}
    return gitio.find_renames(project.root, sorted(missing))


def open_hasher(project):
    mode = "git" if gitio.in_git(project.root) else "raw"
    return Hasher(project.root, project.state_dir / "cache" / "stat.json", mode=mode)


def evaluate(project, hasher):
    claims = load_claims(project)
    sources = [s for c in claims if c.status == "verified" and not c.parse_error
               for s in c.sources if safe_source(project.root, s.path) is not None]
    hasher.prime([s.path for s in sources])
    anchors = anchors_for(project, sources)
    renames = renames_for(project, sources)
    results = []
    for c in claims:
        state, per = freshness(c, project, hasher, anchors, renames=renames)
        claim_renames = {s.path: renames[s.path] for s in c.sources if s.path in renames}
        results.append(Result(c, problems(c, project), state, per, claim_renames))
    return results
