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

    def test_sources_block_absorbs_interior_blank_lines_but_preserves_trailing_ones(self):
        # `parse` tolerates a blank line inside a list (it's absorbed as long as
        # another indented item follows); `rewrite`'s block-consumption must
        # match that exactly, or the blank line's item gets left behind as a
        # stray line and re-appears as a duplicate on the next parse.
        text = "---\nid: x\nsources:\n  - src_a.py\n\n  - src_b.py\n\nstatus: unverified\n---\nb\n"
        new = rewrite(text, "x.md", sources=[{"path": "src_a.py"}, {"path": "src_b.py"}])
        self.assertEqual(new, "---\nid: x\nsources:\n  - path: src_a.py\n  - path: src_b.py"
                              "\n\nstatus: unverified\n---\nb\n")
        # and re-parsing the result finds exactly two sources, not three
        fm, _ = split(new, "x.md")
        self.assertEqual(parse(fm, "x.md")["sources"], [{"path": "src_a.py"}, {"path": "src_b.py"}])

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


if __name__ == "__main__":
    unittest.main()
