"""Operations that change files. Each refuses rather than guessing."""
import os
from datetime import date
from pathlib import Path

from . import claims as C
from . import frontmatter, gitio
from .frontmatter import quote
from .project import CONFIG, safe_source


class Refused(Exception):
    pass


class NeedsIdentity(Exception):
    """No one to record: no --to and no git user.email. Exit 2."""


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
    # Default claims dir name — init always writes the default config, so the
    # store it creates is always "claims/". A clone checked out with
    # core.autocrlf=true otherwise delivers claim files as CRLF, which every
    # claimlock command that parses them rejects (spec amendment T9).
    ga = root / ".gitattributes"
    ga_line = "claims/*.md text eol=lf"
    ga_existing = ga.read_text(encoding="utf-8") if ga.exists() else ""
    if ga_line not in ga_existing.splitlines():
        sep = "" if (not ga_existing or ga_existing.endswith("\n")) else "\n"
        ga.write_text(ga_existing + sep + ga_line + "\n", encoding="utf-8")
        created.append(f".gitattributes (+ {ga_line})")
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


def _write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def verify(project, cid) -> list:
    """Pin every source of `cid` to its current content and mark it verified.

    Refuses a conflicted claim, a refuted claim, a claim that would be
    invalid as verified (no evidence, no sources, bad fields), and a claim
    with a missing source. Nothing is written unless every check passes.
    """
    all_claims = C.load_claims(project)
    c = next((x for x in all_claims if x.id == cid), None)
    if c is None:
        raise Refused(f"no claim {cid!r}")
    if c.conflicted:
        raise Refused(f"{cid} has merge conflicts — run claimlock resolve first")
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
            # Hasher.blob swallows OSError (one bad source must not silence
            # every other claim), so the specific reason is lost by the time
            # we get here — recover it with a direct open attempt on the same
            # safe path, purely for the error message.
            detail = ""
            p = safe_source(project.root, s.path)
            if p is not None:
                try:
                    p.open("rb").close()
                except OSError as e:
                    if e.strerror:
                        detail = f" ({e.strerror})"
            raise Refused(f"{cid}: source {s.path} does not exist or cannot be read{detail} — "
                          f"fix its sources (or the file's permissions), then verify")
        pinned.append((s.path, blob))
    text = frontmatter.rewrite(c.text, c.path.name, status="verified",
                               sources=[{"path": p, "blob": b} for p, b in pinned],
                               remove=("verified_at", "owed_by", "owed_since"))
    _write(c.path, text)
    return pinned


def owe(project, cid, to=None, reason=None, today=None):
    """Hand off a re-check: status owed, owed_by, owed_since. Refuses a claim
    with problems, an unverified/refuted claim, a fresh claim, and a claim
    already owed to the same person."""
    c = next((x for x in C.load_claims(project) if x.id == cid), None)
    if c is None:
        raise Refused(f"no claim {cid!r}")
    if c.conflicted or c.parse_error or C.problems(c, project):
        raise Refused(f"{cid} has problems that must be fixed first (run: claimlock check)")
    if c.status in ("unverified", "refuted"):
        raise Refused(f"{cid} is {c.status}; only a verified or owed claim can be owed")
    email = to or gitio.user_email(project.root)
    if not email:
        raise NeedsIdentity("nobody to hand this to — pass --to <email> or set git config user.email")
    if not C.EMAIL_RE.match(email):
        raise Refused(f"{email!r} is not an email address")
    if c.status == "verified":
        hasher = C.open_hasher(project)
        state, _ = C.freshness(c, project, hasher, C.anchors_for(project, c.sources))
        hasher.save()
        if state == "fresh":
            raise Refused(f"{cid} is fresh — nothing is owed")
    elif c.owed_by == email:
        raise Refused(f"{cid} is already owed by {email}")
    since = gitio.short_head(project.root) or "none"
    text = frontmatter.rewrite(c.text, c.path.name, status="owed",
                               set_fields={"owed_by": email, "owed_since": since})
    if reason:
        who = gitio.user_email(project.root) or "unknown"
        day = today or date.today().isoformat()
        text = text.rstrip("\n") + f"\n\nOwed {day} by {who}: {reason}\n"
    _write(c.path, text)
    return email, since
