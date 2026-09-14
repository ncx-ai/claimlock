"""Strict frontmatter: a small YAML subset parsed with the standard library.

Accepted shapes, and nothing else:

    key: scalar
    key: []
    key:                 (empty value -> None, or a list if items follow)
      - scalar
      - k: scalar        (a flat map; continuation keys indented 4 spaces)
        k2: scalar

Scalars are plain, 'single-quoted' or "double-quoted" (JSON escapes). A `#`
starts a comment at line start or after whitespace in a plain scalar. All
values are strings: no numbers, booleans or dates are inferred.

Anything else is an error naming the file and line. Guessing would silently
change what a claim says, and a claim store that misreads claims is worse than
one that refuses them.
"""
import json
import re

KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*))?$")
ITEM = re.compile(r"^  - (.*)$")
CONT = re.compile(r"^    ([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*))?$")
MAP_START = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*))?$")
SPECIAL_START = set("[{&*!|>%@`")


class FrontmatterError(Exception):
    def __init__(self, name, line, msg):
        super().__init__(f"{name}:{line}: {msg}")
        self.name, self.line, self.msg = name, line, msg


def split(text, name):
    """Return (frontmatter lines, body). Line 1 of the file is the opening '---'."""
    lines = text.split("\n")
    if lines[0] != "---":
        raise FrontmatterError(name, 1, "file must start with a '---' line")
    for i in range(1, len(lines)):
        if lines[i] == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    raise FrontmatterError(name, 1, "no closing '---' line")


def _blank_or_comment(line):
    s = line.strip()
    return not s or s.startswith("#")


def _scalar(raw, name, ln):
    s = (raw or "").strip()
    if s == "" or s.startswith("#"):
        return None
    if s[0] == '"':
        try:
            val, end = json.JSONDecoder().raw_decode(s)
        except ValueError as e:
            raise FrontmatterError(name, ln, f"bad double-quoted string: {e}") from None
        if not isinstance(val, str):
            raise FrontmatterError(name, ln, "bad double-quoted string")
        rest = s[end:].strip()
        if rest and not rest.startswith("#"):
            raise FrontmatterError(name, ln, "text after closing quote")
        return val
    if s[0] == "'":
        m = re.match(r"^'((?:[^']|'')*)'[ \t]*(#.*)?$", s)
        if not m:
            raise FrontmatterError(name, ln, "bad single-quoted string")
        return m.group(1).replace("''", "'")
    s = re.sub(r"[ \t]+#.*$", "", s)
    if s == "[]":
        return []
    if s[0] in SPECIAL_START:
        raise FrontmatterError(name, ln, f"unsupported YAML syntax starting with {s[0]!r}; quote the value")
    return s


def parse(lines, name):
    meta = {}
    i, n = 0, len(lines)
    while i < n:
        ln = i + 2
        line = lines[i]
        if _blank_or_comment(line):
            i += 1
            continue
        m = KEY.match(line)
        if not m:
            if line[:1] in (" ", "\t"):
                raise FrontmatterError(name, ln, "unexpected indentation; expected 'key: value' at column 0")
            raise FrontmatterError(name, ln, "expected 'key: value' at column 0")
        key, raw = m.group(1), m.group(2)
        if key in meta:
            raise FrontmatterError(name, ln, f"duplicate key {key!r}")
        i += 1
        value = _scalar(raw, name, ln)
        if value is not None:
            meta[key] = value
            continue
        items = []
        while i < n:
            line = lines[i]
            if _blank_or_comment(line):
                i += 1
                continue
            im = ITEM.match(line)
            if not im:
                break
            item_ln = i + 2
            i += 1
            first = im.group(1)
            km = MAP_START.match(first)
            if km and first[:1] not in ("'", '"'):
                obj = {km.group(1): _scalar(km.group(2), name, item_ln)}
                while i < n:
                    cm = CONT.match(lines[i])
                    if not cm:
                        break
                    k = cm.group(1)
                    if k in obj:
                        raise FrontmatterError(name, i + 2, f"duplicate key {k!r}")
                    obj[k] = _scalar(cm.group(2), name, i + 2)
                    i += 1
                items.append(obj)
            else:
                items.append(_scalar(first, name, item_ln))
        if i < n and not _blank_or_comment(lines[i]) and not KEY.match(lines[i]):
            raise FrontmatterError(
                name, i + 2,
                "unexpected indentation; list items are '  - ' and map continuations are 4 spaces")
        meta[key] = items if items else None
    return meta


_NEEDS_QUOTE = re.compile(r"""[:#'"\[\]{},&*!|>%@`]|^\s|\s$|^-|^$""")


def quote(s):
    """Render a string as a scalar that `parse` reads back unchanged."""
    return json.dumps(s, ensure_ascii=False) if _NEEDS_QUOTE.search(s) else s


def _sources_block(sources):
    if not sources:
        return ["sources: []"]
    out = ["sources:"]
    for s in sources:
        out.append(f"  - path: {quote(s['path'])}")
        if s.get("blob"):
            out.append(f"    blob: {s['blob']}")
    return out


def rewrite(text, name, *, status=None, verified_at=None, sources=None):
    """Replace only the named fields; every other line is preserved in place.

    None means "leave untouched". A missing `status`/`verified_at` key is
    inserted after `status` (or after `id`, or at the end); a missing
    `sources` key is appended at the end of the frontmatter.
    """
    fm, _ = split(text, name)  # validates the delimiters
    lines = text.split("\n")
    close = len(fm) + 1
    head = lines[1:close]

    if sources is not None:
        out, i, placed = [], 0, False
        while i < len(head):
            if re.match(r"^sources:", head[i]):
                i += 1
                while i < len(head) and head[i][:1] in (" ", "\t"):
                    i += 1
                out.extend(_sources_block(sources))
                placed = True
                continue
            out.append(head[i])
            i += 1
        if not placed:
            out.extend(_sources_block(sources))
        head = out

    def set_scalar(key, value, after):
        for j, l in enumerate(head):
            if re.match(rf"^{key}:", l):
                head[j] = f"{key}: {value}"
                return
        idx = next((j for j, l in enumerate(head) if re.match(rf"^{after}:", l)), None)
        if idx is None:
            idx = next((j for j, l in enumerate(head) if re.match(r"^id:", l)), len(head) - 1)
        head.insert(idx + 1, f"{key}: {value}")

    if status is not None:
        set_scalar("status", status, "area")
    if verified_at is not None:
        set_scalar("verified_at", verified_at, "status")
    return "\n".join(["---", *head, *lines[close:]])
