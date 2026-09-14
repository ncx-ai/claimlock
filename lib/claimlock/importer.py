"""Import claims written in the original ground-truth format.

That format lists `sources` as plain paths and has no pins. Every imported
claim therefore arrives UNPINNED, and `check` fails until each one is
re-checked and verified. That is deliberate: an import must not launder old
verifications into fresh pins. Everything except the sources block is copied
byte-for-byte.
"""
from pathlib import Path

from . import frontmatter
from .claims import StoreMissing


def import_dir(project, src):
    if not project.claims_dir.is_dir():
        raise StoreMissing(project.claims_dir)
    imported, errors = [], []
    for p in sorted(Path(src).glob("*.md")):
        if p.name == "README.md":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            fm, _ = frontmatter.split(text, p.name)
            meta = frontmatter.parse(fm, p.name)
        except frontmatter.FrontmatterError as e:
            errors.append(f"skipped {e}")
            continue
        dest = project.claims_dir / p.name
        if dest.exists():
            errors.append(f"skipped {p.name}: {dest} already exists")
            continue
        raw = meta.get("sources") or []
        paths = []
        for e in raw if isinstance(raw, list) else []:
            if isinstance(e, str):
                paths.append(e)
            elif isinstance(e, dict) and isinstance(e.get("path"), str):
                paths.append(e["path"])
        dest.write_text(frontmatter.rewrite(text, p.name, sources=[{"path": x} for x in paths]),
                        encoding="utf-8")
        imported.append(p.stem)
    return imported, errors
