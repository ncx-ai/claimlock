### Task 4: Claims — loading, validation, freshness

**Files:**
- Create: `lib/claimlock/claims.py`, `tests/test_claims.py`

**Interfaces:**
- Consumes: `frontmatter.split/parse/FrontmatterError` (Task 2); `project.safe_source`, `Project` (Task 3); `pins.Hasher` (Task 3); helpers.
- Produces: `StoreMissing`, `STATUSES`, `KINDS`, `FIELDS`, `ID_RE`, `NON_FRESH`, `Source`, `Claim` (properties `id, area, status, verified_at, evidence, sources`; `headline()`), `Result(claim, problems, state, per_source)`, `load_claims(project)`, `problems(claim, project, *, as_status=None)`, `freshness(claim, project, hasher)`, `evaluate(project, hasher)`, `open_hasher(project)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_claims.py`:
```python
import unittest

from helpers import TmpCase, claim_text, make_repo, write
from claimlock.claims import StoreMissing, evaluate, freshness, load_claims, open_hasher, problems
from claimlock.pins import Hasher, blob_of_bytes
from claimlock.project import load


def pinned_text(cid, pins, status="verified"):
    lines = ["---", f"id: {cid}", "area: core", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c", "sources:"]
    for path, blob in pins:
        lines.append(f"  - path: {path}")
        if blob:
            lines.append(f"    blob: {blob}")
    return "\n".join(lines + ["---", "Holds.", ""])


class Load(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def test_missing_dir_raises(self):
        (self.root / "claims").rmdir()
        with self.assertRaises(StoreMissing):
            load_claims(load(self.root))

    def test_readme_ignored_and_sorted(self):
        write(self.root, "claims/README.md", "# not a claim\n")
        write(self.root, "claims/b.md", claim_text("b"))
        write(self.root, "claims/a.md", claim_text("a"))
        self.assertEqual([c.id for c in load_claims(load(self.root))], ["a", "b"])

    def test_parse_error_becomes_the_only_problem(self):
        write(self.root, "claims/bad.md", "---\nid: bad\nsources: [x]\n---\nx\n")
        p = load(self.root)
        [c] = load_claims(p)
        self.assertEqual(c.id, "bad")
        [msg] = problems(c, p)
        self.assertIn("bad.md:3", msg)
        self.assertIn("unsupported", msg)

    def test_crlf_rejected(self):
        write(self.root, "claims/c.md", claim_text("c").replace("\n", "\r\n"))
        p = load(self.root)
        [c] = load_claims(p)
        self.assertIn("CR line endings", problems(c, p)[0])

    def test_properties(self):
        write(self.root, "claims/c.md", claim_text("c", sources=("a.py",), body="First line.\n\nMore."))
        [c] = load_claims(load(self.root))
        self.assertEqual((c.area, c.status, c.headline()), ("core", "unverified", "First line."))
        self.assertEqual([(s.path, s.blob) for s in c.sources], [("a.py", None)])


class Problems(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def probs(self, text, **kw):
        write(self.root, "claims/c.md", text)
        p = load(self.root)
        [c] = load_claims(p)
        return problems(c, p, **kw)

    def test_valid_unverified_claim_has_none(self):
        self.assertEqual(self.probs(claim_text("c")), [])

    def test_each_rule(self):
        cases = [
            ("unknown", claim_text("c", extra_lines=["color: red"]), "unknown field 'color'"),
            ("mismatch", claim_text("other"), "does not match filename"),
            ("no-id", "---\narea: x\n---\nbody\n", "missing 'id'"),
            ("status", claim_text("c", status="maybe"), "status 'maybe'"),
            ("body", claim_text("c", body=""), "no claim text"),
            ("kind", claim_text("c", evidence=(("vibes", "x"),)), "evidence kind 'vibes'"),
            ("ref", "---\nid: c\nevidence:\n  - kind: test\n---\nb\n", "needs 'kind' and 'ref'"),
            ("escape", claim_text("c", sources=("../outside.py",)), "stay inside"),
            ("dup", claim_text("c", sources=("a.py", "a.py")), "listed twice"),
            ("blob", "---\nid: c\nsources:\n  - path: a.py\n    blob: nothex\n---\nb\n", "malformed blob"),
            ("src-key", "---\nid: c\nsources:\n  - path: a.py\n    sha: x\n---\nb\n", "unknown keys"),
            ("no-evidence", claim_text("c", status="verified", evidence=(), sources=("a.py",)), "no evidence"),
            ("no-sources", claim_text("c", status="verified"), "can never go stale"),
        ]
        for name, text, needle in cases:
            with self.subTest(name):
                got = self.probs(text)
                self.assertTrue(any(needle in g for g in got), f"{needle!r} not in {got}")

    def test_as_status_applies_verified_rules(self):
        text = claim_text("c")
        self.assertEqual(self.probs(text), [])
        self.assertTrue(any("can never go stale" in g for g in self.probs(text, as_status="verified")))


class Freshness(TmpCase):
    def check(self, use_git):
        root = make_repo(self.tmp / f"r{use_git}", use_git=use_git)
        p = load(root)
        write(root, "a.py", "one\n")
        write(root, "b.py", "bee\n")
        blob_a = blob_of_bytes(b"one\n")
        blob_b = blob_of_bytes(b"bee\n")

        def state(text):
            write(root, "claims/c.md", text)
            [c] = load_claims(p)
            return freshness(c, p, open_hasher(p))

        self.assertEqual(state(pinned_text("c", [("a.py", blob_a)])), ("fresh", [("a.py", "fresh")]))
        self.assertEqual(state(pinned_text("c", [("a.py", None)])), ("unpinned", [("a.py", "unpinned")]))
        self.assertEqual(state(pinned_text("c", [("a.py", blob_a)], status="unverified")), (None, []))
        write(root, "a.py", "two!\n")
        self.assertEqual(state(pinned_text("c", [("a.py", blob_a)])), ("stale", [("a.py", "stale")]))
        (root / "b.py").unlink()
        self.assertEqual(state(pinned_text("c", [("a.py", blob_a), ("b.py", blob_b)])),
                         ("missing", [("a.py", "stale"), ("b.py", "missing")]))

    def test_without_git(self):
        self.check(False)

    def test_with_git(self):
        import shutil
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.check(True)

    def test_evaluate_counts_hashed_sources(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "b.py", "bee\n")
        write(root, "claims/c.md", pinned_text("c", [("a.py", None), ("b.py", None)]))
        write(root, "claims/d.md", claim_text("d"))
        p = load(root)
        h = Hasher(root, None)
        results = evaluate(p, h)
        self.assertEqual([(r.claim.id, r.state) for r in results], [("c", "unpinned"), ("d", None)])
        self.assertEqual(h.hashed, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: `ModuleNotFoundError: No module named 'claimlock.claims'`.

- [ ] **Step 3: Implement `lib/claimlock/claims.py`**

```python
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
            text = p.read_text(encoding="utf-8")
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK`. `test_claims` contributes 11 tests (Load 5, Problems 3, Freshness 3); total `Ran 33 tests`.

- [ ] **Step 5: Watch staleness detection fail for its own reason**

Temporarily change `elif cur != s.blob:` to `elif False:`; run; expected: `test_without_git` and `test_with_git` FAIL on the `stale` assertion (`('fresh', ...) != ('stale', ...)`). Re-apply the inverse edit; rerun; OK.

- [ ] **Step 6: Commit**

```bash
git add lib/claimlock/claims.py tests/test_claims.py
git commit -m "feat: claim loading, validation and content freshness" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
