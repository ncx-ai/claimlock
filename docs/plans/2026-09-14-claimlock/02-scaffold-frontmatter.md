### Task 2: Scaffold, launcher, test helpers, spec amendments, frontmatter parser

**Files:**
- Create: `bin/claimlock`, `lib/claimlock/__init__.py`, `lib/claimlock/frontmatter.py`, `tests/helpers.py`, `tests/test_frontmatter.py`, `.gitignore`
- Modify: `docs/specs/2026-09-14-claimlock-design.md` (apply the seven amendments listed in `00-index.md` — edit the affected sentences in §2, §3, §4, §5 and add an "Amendments (2026-09-14, planning)" list at the end)

**Interfaces:**
- Consumes: nothing.
- Produces: `frontmatter.split/parse/rewrite/quote`, `FrontmatterError`; `tests/helpers.py` with `TmpCase`, `make_repo`, `write`, `claim_text`, `run_cli`, `git`, `REPO`, `BIN`.

- [ ] **Step 1: Scaffold**

`.gitignore`:
```
__pycache__/
*.pyc
.claimlock/
```

`lib/claimlock/__init__.py`:
```python
"""claimlock — claims pinned to the content that could falsify them."""
VERSION = "0.1.0"
```

`bin/claimlock` (then `chmod +x bin/claimlock`):
```python
#!/usr/bin/env python3
"""claimlock launcher. The package lives in ../lib so the plugin exposes one command."""
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stderr.write("claimlock: needs Python 3.11+ (found %d.%d)\n" % sys.version_info[:2])
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from claimlock.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
```

`tests/helpers.py`:
```python
"""Shared test fixtures. Every test gets a fresh temp directory."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin" / "claimlock"
sys.path.insert(0, str(REPO / "lib"))


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def make_repo(root: Path, use_git: bool, config: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if use_git:
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "t")
        git(root, "config", "commit.gpgsign", "false")
    (root / ".claimlock.toml").write_text(config)
    (root / "claims").mkdir()
    return root


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def claim_text(cid, status="unverified", evidence=(("test", "suite::case"),), sources=(),
               area="core", body="The thing holds.", extra_lines=()):
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}"]
    lines.append("evidence:" if evidence else "evidence: []")
    for kind, ref in evidence:
        lines += [f"  - kind: {kind}", f"    ref: {ref}"]
    lines.append("sources:" if sources else "sources: []")
    for s in sources:
        lines.append(f"  - path: {s}")
    lines += list(extra_lines)
    lines += ["---", body, ""]
    return "\n".join(lines)


def run_cli(cwd, *args, stdin=None, env=None):
    e = dict(os.environ)
    e["NO_COLOR"] = "1"
    e.update(env or {})
    r = subprocess.run([sys.executable, str(BIN), *args], cwd=cwd, capture_output=True,
                       text=True, input=stdin, env=e, timeout=120)
    return r.returncode, r.stdout, r.stderr


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()

    def tearDown(self):
        self._td.cleanup()
```

- [ ] **Step 2: Write the failing tests**

`tests/test_frontmatter.py`:
```python
import unittest

from helpers import TmpCase  # noqa: F401  (puts lib/ on sys.path)
from claimlock.frontmatter import FrontmatterError, parse, quote, rewrite, split

DOC = """---
id: a-claim
area: core   # trailing comment
status: verified
verified_at: 2026-09-14T14:38:58-04:00
evidence:
  - kind: test
    ref: "suite::case: with colon"
  - kind: measurement
    ref: 'it''s 6172 vs 6000'
sources:
  - path: src/a.py
    blob: 0123456789abcdef0123456789abcdef01234567
  - src/b.py
---
The claim.

Body line.
"""


def meta_of(text, name="a-claim.md"):
    fm, body = split(text, name)
    return parse(fm, name), body


class Parse(unittest.TestCase):
    def test_full_document(self):
        meta, body = meta_of(DOC)
        self.assertEqual(meta["id"], "a-claim")
        self.assertEqual(meta["area"], "core")
        self.assertEqual(meta["verified_at"], "2026-09-14T14:38:58-04:00")
        self.assertEqual(meta["evidence"], [
            {"kind": "test", "ref": "suite::case: with colon"},
            {"kind": "measurement", "ref": "it's 6172 vs 6000"},
        ])
        self.assertEqual(meta["sources"], [
            {"path": "src/a.py", "blob": "0123456789abcdef0123456789abcdef01234567"},
            "src/b.py",
        ])
        self.assertEqual(body, "The claim.\n\nBody line.\n")

    def test_empty_list_and_empty_value(self):
        meta, _ = meta_of("---\nevidence: []\nverified_at:\n---\nx\n")
        self.assertEqual(meta["evidence"], [])
        self.assertIsNone(meta["verified_at"])

    def test_url_item_is_a_scalar_not_a_map(self):
        meta, _ = meta_of("---\nsources:\n  - http://x/y\n---\nx\n")
        self.assertEqual(meta["sources"], ["http://x/y"])

    def test_errors_name_the_line(self):
        cases = [
            ("no-open.md", "id: x\n---\n", 1, "must start"),
            ("no-close.md", "---\nid: x\n", 1, "closing"),
            ("dup.md", "---\nid: x\nid: y\n---\n", 3, "duplicate"),
            ("indent.md", "---\nsources:\n   - a\n---\n", 3, "indentation"),
            ("flow.md", "---\nsources: [a, b]\n---\n", 2, "unsupported"),
            ("badkey.md", "---\n  id: x\n---\n", 2, "key: value"),
            ("dq.md", '---\nid: "unterminated\n---\n', 2, "double-quoted"),
            ("after.md", '---\nid: "x" y\n---\n', 2, "after closing quote"),
        ]
        for name, text, line, needle in cases:
            with self.subTest(name):
                with self.assertRaises(FrontmatterError) as cm:
                    meta_of(text, name)
                self.assertEqual(cm.exception.line, line, str(cm.exception))
                self.assertIn(needle, str(cm.exception))
                self.assertIn(name, str(cm.exception))


class Quote(unittest.TestCase):
    def test_round_trips_through_parse(self):
        for s in ["plain/path.py", "has: colon", "has #hash", "'quoted'", "- dash", "", " lead", "ünï"]:
            with self.subTest(s):
                meta, _ = meta_of(f"---\nk: {quote(s)}\n---\n")
                self.assertEqual(meta["k"], s if s != "" else "")


class Rewrite(unittest.TestCase):
    def test_replaces_only_named_parts_in_place(self):
        new = rewrite(DOC, "a-claim.md", status="verified", verified_at="2026-09-15T10:00:00+00:00",
                      sources=[{"path": "src/a.py", "blob": "f" * 40}, {"path": "src/b.py", "blob": "e" * 40}])
        old_lines, new_lines = DOC.split("\n"), new.split("\n")
        self.assertIn("verified_at: 2026-09-15T10:00:00+00:00", new_lines)
        start = new_lines.index("sources:")
        self.assertEqual(new_lines[start:start + 5], [
            "sources:", "  - path: src/a.py", f"    blob: {'f' * 40}", "  - path: src/b.py", f"    blob: {'e' * 40}"])
        # every line outside verified_at and the sources block is byte-identical, in order
        keep = lambda ls: [l for l in ls if not l.startswith(("verified_at:", "sources:", "  - path:", "    blob:", "  - src/"))]
        self.assertEqual(keep(old_lines), keep(new_lines))
        self.assertTrue(new.endswith("The claim.\n\nBody line.\n"))

    def test_sources_block_stays_where_it_was(self):
        text = "---\nid: x\nsources:\n  - a\nstatus: unverified\n---\nb\n"
        new = rewrite(text, "x.md", sources=[{"path": "a", "blob": "1" * 40}])
        self.assertEqual(new, f"---\nid: x\nsources:\n  - path: a\n    blob: {'1' * 40}\nstatus: unverified\n---\nb\n")

    def test_inserts_missing_keys_after_status(self):
        text = "---\nid: x\nstatus: unverified\n---\nb\n"
        new = rewrite(text, "x.md", status="verified", verified_at="T", sources=[])
        self.assertEqual(new, "---\nid: x\nstatus: verified\nverified_at: T\nsources: []\n---\nb\n")

    def test_none_leaves_field_untouched(self):
        self.assertEqual(rewrite(DOC, "a-claim.md"), DOC)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'claimlock.frontmatter'`.

- [ ] **Step 4: Implement `lib/claimlock/frontmatter.py`**

```python
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
```

Note `test_inserts_missing_keys_after_status`: with `sources=[]` and no `sources` key, the block is appended at the end of the frontmatter, then `verified_at` is inserted after `status`. Expected output is exactly `"---\nid: x\nstatus: verified\nverified_at: T\nsources: []\n---\nb\n"`.

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests in `test_frontmatter` PASS (count them: 9 test methods, `test_errors_name_the_line` has 8 subtests). `Ran 9 tests` and `OK`.

- [ ] **Step 6: Watch one detector fail for its own reason**

Temporarily change `if key in meta:` to `if False:` in `parse`; run the suite; expected: `test_errors_name_the_line` (subTest `dup.md`) FAILS with `FrontmatterError not raised`. Re-apply the inverse edit (do NOT use `git checkout --`); rerun; OK.

- [ ] **Step 7: Apply spec amendments**

Edit `docs/specs/2026-09-14-claimlock-design.md` so it states the seven amendments from `00-index.md` in the sections they affect (layout tree in §2; parse error → invalid in §3 per-claim states and §4 exit codes and §7 "malformed frontmatter" bullet; Stop baseline replacement in §5; `verify` sets status in §4; stat-cache 2 s guard in §3; dangling identity `path:id` in §5; README ignored in §3 claim file). Append:
```markdown
## Amendments (2026-09-14, planning)

1. Layout is `bin/claimlock` launcher + `lib/claimlock/` package.
2. Unparseable claim frontmatter is `invalid` (exit 1), not exit 2.
3. Stop replaces its baseline with the current survey.
4. `verify` sets `status: verified`; refuses `refuted`.
5. Stat cache skips entries with mtime < 2 s old.
6. Dangling-marker identity is `path:id`.
7. `claims_dir/README.md` is not loaded as a claim.
```

- [ ] **Step 8: Commit**

```bash
git add .gitignore bin lib tests docs/specs
git commit -m "feat: scaffold, launcher and strict frontmatter parser" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
