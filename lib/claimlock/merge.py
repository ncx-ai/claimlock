"""Resolve git merge conflicts in claim files — the `sources` pins only.

A pin is kept only when it equals the merged working-tree content of its
source, so every kept pin is exactly what one side verified. When a source
matches neither side, the claim becomes `owed` by whoever is merging.
Conflicts anywhere else (prose, evidence, other fields) are left for a person,
because no rule can decide them.
"""
import re
from dataclasses import dataclass

from . import claims as C
from . import frontmatter, gitio
from .project import safe_source

START, BASE, MID, END = "<<<<<<< ", "||||||| ", "=======", ">>>>>>> "
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")
_SOURCE_LINE = re.compile(r"^(sources:.*|  - .*|    [A-Za-z_][A-Za-z0-9_]*:.*|\s*)$")


@dataclass
class Hunk:
    start: int
    end: int
    ours: list
    theirs: list


def hunks(lines):
    """Conflict hunks, supporting diff3 base sections. ValueError when unbalanced."""
    out, i, n = [], 0, len(lines)
    while i < n:
        if lines[i].startswith(START):
            start, ours, theirs, part = i, [], [], "ours"
            i += 1
            while i < n and not lines[i].startswith(END):
                line = lines[i]
                if line.startswith(BASE):
                    part = "base"
                elif line == MID:
                    part = "theirs"
                elif part == "ours":
                    ours.append(line)
                elif part == "theirs":
                    theirs.append(line)
                i += 1
            if i == n:
                raise ValueError("unterminated conflict hunk")
            out.append(Hunk(start, i, ours, theirs))
        elif lines[i].startswith(END):
            raise ValueError(f"conflict end marker without a start (line {i + 1})")
        i += 1
    return out


def _side(lines, hs, which):
    out, i = [], 0
    for h in hs:
        out.extend(lines[i:h.start])
        out.extend(h.ours if which == "ours" else h.theirs)
        i = h.end + 1
    out.extend(lines[i:])
    return "\n".join(out)


def _frontmatter_close(lines, inside):
    """Index of the frontmatter's closing `---`, read from lines outside every
    hunk; None when the opening delimiter is missing or inside a hunk. A
    closing delimiter that sits inside a hunk makes the first `---` found here
    a body line instead, but the hunk holding the real one then contains a
    `---` line, which `_in_sources` rejects."""
    if not lines or 0 in inside or lines[0] != "---":
        return None
    return next((j for j in range(1, len(lines)) if j not in inside and lines[j] == "---"), None)


def _in_sources(lines, inside, close, h):
    """True only for a hunk wholly inside the frontmatter's `sources` block.
    Confined to the frontmatter first: a body line may start with `sources:`.

    Bounded at the top as well as the bottom, per side: a side that holds the
    `sources:` line may have only blank lines before it (anything else — an
    evidence entry's `    ref:` line, say — belongs to the field above, and
    resolving would silently pick "ours" for that conflict), and a side that
    does not hold it must sit under a preceding `sources:` key."""
    if close is None or h.end >= close:
        return False
    if not all(_SOURCE_LINE.match(line) for line in h.ours + h.theirs):
        return False
    j, above = h.start - 1, None
    while j > 0:  # line 0 is the opening delimiter
        if j not in inside and _KEY.match(lines[j]):
            above = lines[j]
            break
        j -= 1
    for side in (h.ours, h.theirs):
        at = next((i for i, line in enumerate(side) if line.startswith("sources:")), None)
        if at is None:
            if not (above or "").startswith("sources:"):
                return False
        elif any(line.strip() for line in side[:at]):
            return False
    return True


def _pins(text, name):
    fm, _ = frontmatter.split(text, name)
    meta = frontmatter.parse(fm, name)
    out = []
    for e in meta.get("sources") or []:
        if isinstance(e, dict) and isinstance(e.get("path"), str):
            out.append((e["path"], e.get("blob") if isinstance(e.get("blob"), str) else None))
        elif isinstance(e, str):
            out.append((e, None))
    return out


def resolve_claim(project, claim):
    """("kept" | "owed" | "left", message). Writes the claim only for kept/owed."""
    name = claim.path.name
    lines = claim.text.split("\n")
    try:
        hs = hunks(lines)
    except ValueError as e:
        return "left", f"{name}: {e}"
    if not hs:
        return "left", f"{name}: no conflict markers"
    inside = {k for h in hs for k in range(h.start, h.end + 1)}
    close = _frontmatter_close(lines, inside)
    if close is None:
        return "left", f"{name}: a frontmatter delimiter is inside a conflict — needs a person"
    for h in hs:
        if not _in_sources(lines, inside, close, h):
            return "left", f"{name}: a conflict outside the sources block (line {h.start + 1}) needs a person"
    ours_text, theirs_text = _side(lines, hs, "ours"), _side(lines, hs, "theirs")
    try:
        ours, theirs = _pins(ours_text, name), _pins(theirs_text, name)
    except frontmatter.FrontmatterError as e:
        return "left", str(e)
    if sorted(p for p, _ in ours) != sorted(p for p, _ in theirs):
        return "left", f"{name}: the two sides cite different sources — resolve by hand"
    theirs_pin = dict(theirs)
    hasher = C.open_hasher(project)
    picked, all_match = [], True
    for path, ours_pin in ours:
        cur = hasher.blob(path, use_cache=False) if safe_source(project.root, path) else None
        if cur is not None and cur == ours_pin:
            picked.append({"path": path, "blob": ours_pin})
        elif cur is not None and cur == theirs_pin.get(path):
            picked.append({"path": path, "blob": theirs_pin[path]})
        else:
            all_match = False
            picked.append({"path": path, "blob": ours_pin} if ours_pin else {"path": path})
    if all_match:
        text = frontmatter.rewrite(ours_text, name, sources=picked)
        outcome, message = "kept", "every source matches one side's verified pin"
    else:
        email = gitio.user_email(project.root)
        if not email:
            return "left", (f"{name}: a source matches neither side, and there is no git user.email "
                            f"to record who owes the re-check — set one, then run claimlock resolve")
        if C.normalize_email(email) is None:
            return "left", (f"{name}: a source matches neither side, and git user.email {email!r} is not "
                            f"an email address to record who owes the re-check — fix it, then run "
                            f"claimlock resolve")
        email = email.strip()
        since = gitio.short_head(project.root) or "none"
        text = frontmatter.rewrite(ours_text, name, status="owed", sources=picked,
                                   set_fields={"owed_by": email, "owed_since": since})
        outcome, message = "owed", f"a source matches neither side — owed by {email}"
    from .ops import _write
    _write(claim.path, text)
    return outcome, message
