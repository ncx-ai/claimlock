"""Operations that change files. Each refuses rather than guessing."""
from pathlib import Path

from . import claims as C
from .frontmatter import quote
from .project import CONFIG


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
