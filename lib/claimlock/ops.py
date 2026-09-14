"""Operations that change files. Each refuses rather than guessing."""
import os
from datetime import datetime
from pathlib import Path

from . import claims as C
from . import frontmatter
from .frontmatter import quote
from .project import CONFIG, safe_source


class Refused(Exception):
    pass


CONFIG_TEMPLATE = """\
# claimlock configuration. Every key is optional; the defaults are shown.
# claims_dir = "claims"
# marker_globs = ["**/*.md"]
# marker_pattern = 'Claim: `([a-z0-9][a-z0-9-]*)`'
"""

CLAIM_TEMPLATE = """\
---
id: {id}
area: {area}
status: unverified
evidence: []
sources: []
---
<One sentence stating the claim, in the present tense.>

<Why it is true: the enforcement site, and what would falsify it. If it is a
number, how it was measured.>
"""


def init_store(root) -> list:
    root = Path(root).resolve()
    cfg = root / CONFIG
    if cfg.exists():
        raise Refused(f"{cfg} already exists")
    created = []
    cfg.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    created.append(CONFIG)
    claims_dir = root / "claims"
    if not claims_dir.is_dir():
        claims_dir.mkdir()
        created.append("claims/")
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".claimlock/" not in existing.splitlines():
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        gi.write_text(existing + sep + ".claimlock/\n", encoding="utf-8")
        created.append(".gitignore (+ .claimlock/)")
    return created


def new_claim(project, cid: str, area: str) -> Path:
    if not C.ID_RE.match(cid):
        raise Refused(f"id {cid!r} is not kebab-case ([a-z0-9][a-z0-9-]*)")
    if not project.claims_dir.is_dir():
        raise C.StoreMissing(project.claims_dir)
    p = project.claims_dir / f"{cid}.md"
    if p.exists():
        raise Refused(f"{p} already exists")
    p.write_text(CLAIM_TEMPLATE.format(id=cid, area=quote(area)), encoding="utf-8")
    return p


def verify(project, cid, now=None) -> list:
    """Pin every source of `cid` to its current content and mark it verified.

    Refuses a refuted claim, a claim that would be invalid as verified (no
    evidence, no sources, bad fields), and a claim with a missing source.
    Nothing is written unless every check passes.
    """
    all_claims = C.load_claims(project)
    c = next((x for x in all_claims if x.id == cid), None)
    if c is None:
        raise Refused(f"no claim {cid!r}")
    if c.status == "refuted":
        raise Refused(f"{cid} is refuted; edit its status by hand if it holds again")
    probs = C.problems(c, project, as_status="verified")
    if probs:
        raise Refused(f"{cid} cannot be verified:\n  " + "\n  ".join(probs))
    hasher = C.open_hasher(project)
    pinned = []
    for s in c.sources:
        blob = hasher.blob(s.path, use_cache=False)
        if blob is None:
            raise Refused(f"{cid}: source {s.path} does not exist or cannot be read — "
                          f"fix its sources (or the file's permissions), then verify")
        pinned.append((s.path, blob))
    stamp = now or datetime.now().astimezone().isoformat(timespec="seconds")
    text = frontmatter.rewrite(c.text, c.path.name, status="verified", verified_at=stamp,
                               sources=[{"path": p, "blob": b} for p, b in pinned])
    tmp = c.path.with_name(c.path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, c.path)
    return pinned
