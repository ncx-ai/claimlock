### Task 4: Data model — `owed`, `conflicted`, `verified_at` removed

Spec §4.1–§4.2. Adds the `owed` status with `owed_by`/`owed_since`, the `conflicted` problem for claim files holding git conflict markers, drops `verified_at` from what `verify` writes (so identical re-verifications produce identical bytes), and gives `frontmatter.rewrite` the ability to set and remove fields.

**Files:**
- Modify: `lib/claimlock/frontmatter.py` (`rewrite`), `lib/claimlock/claims.py` (constants, `Claim`, `load_claims`, `problems`, `freshness`), `lib/claimlock/ops.py` (`verify`, `_write`), `lib/claimlock/cli.py` (`cmd_show` status line, `cmd_list` mark/flag, `MARK`)
- Test: `tests/test_frontmatter.py`, create `tests/test_owed_model.py`, update `tests/test_verify_diff.py`

**Interfaces:**
- Consumes: Task 2/3 `freshness(..., anchors)`, `open_hasher`.
- Produces: `frontmatter.rewrite(..., set_fields=None, remove=())`; `claims.STATUSES` includes `"owed"`; `claims.EMAIL_RE`, `claims.SINCE_RE`; `Claim.conflicted`, `Claim.owed_by`, `Claim.owed_since`; `ops.verify(project, cid)` (no `now`); `ops._write(path, text)`. Task 5 uses all of these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_frontmatter.py` class `Rewrite`:
```python
    def test_set_fields_inserts_after_status_in_order_and_replaces_existing(self):
        text = "---\nid: x\nstatus: verified\nevidence: []\n---\nb\n"
        new = rewrite(text, "x.md", status="owed", set_fields={"owed_by": "bob@example.com", "owed_since": "a1b2c3d"})
        self.assertEqual(new, '---\nid: x\nstatus: owed\nowed_by: "bob@example.com"\nowed_since: a1b2c3d\nevidence: []\n---\nb\n')
        again = rewrite(new, "x.md", set_fields={"owed_by": "amy@example.com"})
        self.assertIn('owed_by: "amy@example.com"\nowed_since: a1b2c3d', again)

    def test_remove_deletes_scalars_and_list_blocks_and_ignores_absent_keys(self):
        text = "---\nid: x\nverified_at: 2026-01-01\nstatus: owed\nowed_by: a@b.c\nsources:\n  - a.py\nevidence: []\n---\nb\n"
        new = rewrite(text, "x.md", remove=("verified_at", "owed_by", "sources", "nope"))
        self.assertEqual(new, "---\nid: x\nstatus: owed\nevidence: []\n---\nb\n")
```
(If `quote` does not quote an email in this codebase, adjust only the two expected strings to what `quote()` produces for `"bob@example.com"`, and state that in the report — the round trip through `parse` must still yield the plain email.)

`tests/test_owed_model.py`:
```python
import unittest

from helpers import TmpCase, claim_text, make_repo, run_cli, write
from claimlock.claims import evaluate, load_claims, open_hasher, problems
from claimlock.project import load

SRC = ("a.py",)


def owed(cid, owed_by="bob@example.com", owed_since="a1b2c3d"):
    lines = []
    if owed_by is not None:
        lines.append(f"owed_by: {owed_by}")
    if owed_since is not None:
        lines.append(f"owed_since: {owed_since}")
    return claim_text(cid, status="owed", sources=SRC, extra_lines=lines)


class OwedStatus(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "one\n")

    def probs(self, text):
        write(self.root, "claims/c.md", text)
        p = load(self.root)
        [c] = load_claims(p)
        return c, problems(c, p)

    def test_valid_owed_claim_has_no_problems_and_no_freshness(self):
        c, got = self.probs(owed("c"))
        self.assertEqual(got, [])
        self.assertEqual((c.owed_by, c.owed_since), ("bob@example.com", "a1b2c3d"))
        [r] = evaluate(load(self.root), open_hasher(load(self.root)))
        self.assertIsNone(r.state)

    def test_owed_rules(self):
        cases = [
            ("no-owner", owed("c", owed_by=None), "not an email address"),
            ("bad-owner", owed("c", owed_by="bob"), "not an email address"),
            ("no-since", owed("c", owed_since=None), "'owed_since'"),
            ("bad-since", owed("c", owed_since="yesterday"), "'owed_since'"),
            ("none-since-ok", owed("c", owed_since="none"), None),
            ("owed-fields-on-verified", claim_text("c", status="verified", sources=SRC,
                                                   extra_lines=["owed_by: bob@example.com"]),
             "only valid with status: owed"),
        ]
        for name, text, needle in cases:
            with self.subTest(name):
                _, got = self.probs(text)
                if needle is None:
                    self.assertEqual(got, [])
                else:
                    self.assertTrue(any(needle in g for g in got), f"{needle!r} not in {got}")

    def test_legacy_verified_at_is_still_accepted(self):
        _, got = self.probs(claim_text("c", extra_lines=["verified_at: 2026-09-08T10:00:00-04:00"]))
        self.assertEqual(got, [])


class Conflicted(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def test_conflict_markers_make_the_claim_conflicted(self):
        text = ("---\nid: c\nstatus: verified\nevidence: []\nsources:\n  - path: a.py\n"
                "<<<<<<< HEAD\n    blob: " + "a" * 40 + "\n=======\n    blob: " + "b" * 40 +
                "\n>>>>>>> origin/main\n---\nHolds.\n")
        write(self.root, "claims/c.md", text)
        p = load(self.root)
        [c] = load_claims(p)
        self.assertTrue(c.conflicted)
        self.assertEqual(c.id, "c")
        [msg] = problems(c, p)
        self.assertIn("conflict markers", msg)
        self.assertIn("claimlock resolve", msg)
        [r] = evaluate(p, open_hasher(p))
        self.assertIsNone(r.state)

    def test_a_setext_heading_is_not_a_conflict(self):
        write(self.root, "claims/c.md", claim_text("c", body="Title\n=======\n\nText."))
        [c] = load_claims(load(self.root))
        self.assertFalse(c.conflicted)


class VerifyWritesNoTimestamp(TmpCase):
    def test_verify_is_byte_identical_when_nothing_changed_and_clears_owed_fields(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=SRC, extra_lines=["verified_at: 2026-01-01"]))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        first = (root / "claims" / "c.md").read_bytes()
        self.assertNotIn(b"verified_at", first)
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual((root / "claims" / "c.md").read_bytes(), first)
        text = first.decode().replace("status: verified", "status: owed\nowed_by: bob@example.com\nowed_since: none")
        write(root, "claims/c.md", text)
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        self.assertEqual((root / "claims" / "c.md").read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
```

In `tests/test_verify_diff.py`, change the assertion `self.assertRegex(text, r"verified_at: \d{4}-…")` in `test_pins_sets_status_and_preserves_body` to `self.assertNotIn("verified_at", text)`.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_owed_model.py" -v` and `-p "test_frontmatter.py"`.
Expected: `rewrite() got an unexpected keyword argument 'set_fields'`; owed claims report `status 'owed' is not one of …`; `Claim` has no `conflicted`; `verified_at` still written.

- [ ] **Step 3: Implement**

`lib/claimlock/frontmatter.py` — change the signature to
`def rewrite(text, name, *, status=None, verified_at=None, sources=None, set_fields=None, remove=()):`,
extend the docstring ("`set_fields` maps key → string value, inserted in order after `status` when absent; `remove` deletes those keys — a scalar line, or a key and its whole list block"), and add, immediately after the `if sources is not None:` block and before `def set_scalar`:
```python
    if remove:
        keys = set(remove)
        out, i = [], 0
        while i < len(head):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", head[i])
            if m and m.group(1) in keys:
                i = _skip_list_block(head, i + 1)
                continue
            out.append(head[i])
            i += 1
        head = out
```
and at the end, after the `verified_at` handling and before the `return`:
```python
    after = "status"
    for key, value in (set_fields or {}).items():
        set_scalar(key, quote(value), after)
        after = key
```

`lib/claimlock/claims.py`:
```python
STATUSES = ("verified", "unverified", "refuted", "owed")
FIELDS = ("id", "area", "status", "verified_at", "owed_by", "owed_since", "evidence", "sources")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
SINCE_RE = re.compile(r"^([0-9a-f]{7,40}|none)$")
_CONFLICT_START = re.compile(r"^<{7} ", re.M)
_CONFLICT_END = re.compile(r"^>{7} ", re.M)
```
(`verified_at` stays in `FIELDS` so legacy files are not flagged as unknown.)
`Claim`: add a field `conflicted: bool = False` after `parse_error`; remove the `verified_at` property; add
```python
    @property
    def owed_by(self):
        return self._str("owed_by")

    @property
    def owed_since(self):
        return self._str("owed_since")
```
`load_claims`: immediately after `text` is decoded (before the CR check), add
```python
        if _CONFLICT_START.search(text) and _CONFLICT_END.search(text):
            out.append(Claim(p, text, {}, "", None, conflicted=True))
            continue
```
`problems`: first lines of the function become
```python
    if claim.conflicted:
        return [f"{claim.path.name}: contains git conflict markers — run `claimlock resolve`"]
    if claim.parse_error:
        return [claim.parse_error]
```
change `for k in ("area", "verified_at"):` to `for k in ("area", "verified_at", "owed_by", "owed_since"):`, and before the `if status == "verified":` block add
```python
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
```
(`as_status` is set only by `verify`, which strips those fields.)
`freshness`: change its guard to `if claim.parse_error or claim.conflicted or claim.status != "verified":`.

`lib/claimlock/ops.py` — add
```python
def _write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
```
Change `def verify(project, cid, now=None)` to `def verify(project, cid)`; before the `c.status == "refuted"` check add `if c.conflicted: raise Refused(f"{cid} has merge conflicts — run claimlock resolve first")`; replace the rewrite + temp-file write with
```python
    text = frontmatter.rewrite(c.text, c.path.name, status="verified",
                               sources=[{"path": p, "blob": b} for p, b in pinned],
                               remove=("verified_at", "owed_by", "owed_since"))
    _write(c.path, text)
```
and remove the unused `datetime` import.

`lib/claimlock/cli.py` — `MARK` gains `"owed": "⇢"`. In `cmd_show` replace the status line with
```python
    line = f"{c.id} ({c.area}) — {c.status}"
    if c.status == "owed":
        line += f", owed by {c.owed_by or '?'} since {c.owed_since or '?'}"
    print(line)
```
In `cmd_list`, after computing `flag`, add `flag += f" [owed → {r.claim.owed_by}]" if r.claim.status == "owed" else ""`.

- [ ] **Step 4: Run to verify they pass**

Run the two focused files, then the full suite plain and with `-W error::ResourceWarning`, and `python3 bin/claimlock self-test`. Report the observed total (+8 tests). If an existing test asserted `verified_at` in `show` output, update it to the new status line and name it in the report.

- [ ] **Step 5: Watch the core guarantee fail for its own reason**

(a) In `verify` temporarily pass `verified_at=datetime.now().astimezone().isoformat(timespec="seconds")` (re-add the import) to `rewrite`; `test_verify_is_byte_identical_when_nothing_changed_and_clears_owed_fields` fails. Re-apply the inverse edits.
(b) In `load_claims` temporarily require only `_CONFLICT_START`; with the start marker alone it still passes, so instead temporarily remove the whole conflict check and confirm `test_conflict_markers_make_the_claim_conflicted` fails. Re-apply. Suite green.

- [ ] **Step 6: Commit**

```bash
git add lib/claimlock/frontmatter.py lib/claimlock/claims.py lib/claimlock/ops.py lib/claimlock/cli.py tests/test_frontmatter.py tests/test_owed_model.py tests/test_verify_diff.py
git commit -m "feat: owed status, conflicted claims, and verify writes no timestamp" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
