import hashlib
import json
import os
import time
import unittest

from helpers import TmpCase, make_repo, run_cli, write
from claimlock.claims import Source, load_claims, pin_digest, problems
from claimlock.pins import Hasher, blob_of_bytes
from claimlock.project import load
from claimlock.regions import RegionError, extract, region_hash

OLD = time.time_ns() - 100 * 1_000_000_000  # 100 s ago: old enough to cache


def region_claim_text(cid, path, region, status="unverified", area="core"):
    """A claim citing one region source, block-style (the frontmatter parser
    has no flow-map syntax)."""
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c",
             "sources:", f"  - path: {path}", f"    region: {region}",
             "---", "Holds.", ""]
    return "\n".join(lines)


class Extract(unittest.TestCase):
    def test_extracts_region_between_markers(self):
        data = b"a\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nz\n"
        self.assertEqual(extract(data, "r1"), "x\ny\n")

    def test_crlf_matches_lf(self):
        lf = b"a\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nz\n"
        crlf = lf.replace(b"\n", b"\r\n")
        self.assertEqual(extract(crlf, "r1"), extract(lf, "r1"))

    def test_empty_region(self):
        data = b"# claimlock:begin r1\n# claimlock:end r1\n"
        self.assertEqual(extract(data, "r1"), "")

    def test_slash_slash_comment_syntax(self):
        data = b"// claimlock:begin r1\nx\n// claimlock:end r1\n"
        self.assertEqual(extract(data, "r1"), "x\n")

    def test_html_comment_syntax(self):
        data = b"<!-- claimlock:begin r1 -->\nx\n<!-- claimlock:end r1 -->\n"
        self.assertEqual(extract(data, "r1"), "x\n")

    def test_longer_name_does_not_open_a_prefix_name(self):
        data = b"# claimlock:begin r1-extra\nx\n# claimlock:end r1-extra\n"
        with self.assertRaises(RegionError) as ctx:
            extract(data, "r1")
        self.assertEqual(str(ctx.exception), "region 'r1' not found")

    def test_nested_regions(self):
        data = (b"# claimlock:begin outer\n"
                b"a\n"
                b"# claimlock:begin inner\n"
                b"b\n"
                b"# claimlock:end inner\n"
                b"c\n"
                b"# claimlock:end outer\n")
        self.assertEqual(extract(data, "outer"),
                          "a\n# claimlock:begin inner\nb\n# claimlock:end inner\nc\n")
        self.assertEqual(extract(data, "inner"), "b\n")

    def test_not_found(self):
        with self.assertRaises(RegionError) as ctx:
            extract(b"x\n", "r1")
        self.assertEqual(str(ctx.exception), "region 'r1' not found")

    def test_begins_more_than_once(self):
        data = b"# claimlock:begin r1\na\n# claimlock:begin r1\nb\n# claimlock:end r1\n"
        with self.assertRaises(RegionError) as ctx:
            extract(data, "r1")
        self.assertEqual(str(ctx.exception), "region 'r1' begins more than once")

    def test_has_no_end_marker(self):
        with self.assertRaises(RegionError) as ctx:
            extract(b"# claimlock:begin r1\nx\n", "r1")
        self.assertEqual(str(ctx.exception), "region 'r1' has no end marker")

    def test_ends_before_it_begins(self):
        data = b"# claimlock:end r1\nx\n# claimlock:begin r1\ny\n"
        with self.assertRaises(RegionError) as ctx:
            extract(data, "r1")
        self.assertEqual(str(ctx.exception), "region 'r1' ends before it begins")

    def test_not_utf8(self):
        with self.assertRaises(RegionError) as ctx:
            extract(b"\xff", "r1")
        self.assertEqual(str(ctx.exception), "not UTF-8, so regions cannot be read")

    def test_region_hash_is_the_blob_hash_of_the_region_text(self):
        self.assertEqual(region_hash("x\ny\n"), blob_of_bytes(b"x\ny\n"))


class Problems(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)

    def probs(self, text, **kw):
        write(self.root, "claims/c.md", text)
        p = load(self.root)
        [c] = load_claims(p)
        return problems(c, p, **kw)

    def test_malformed_region_name(self):
        text = ("---\nid: c\nsources:\n  - path: a.py\n    region: Bad_Name\n"
                "    blob: " + "1" * 40 + "\n    hash: " + "2" * 40 + "\n---\nb\n")
        got = self.probs(text)
        self.assertTrue(any("malformed region name" in g for g in got), got)

    def test_malformed_hash(self):
        text = ("---\nid: c\nsources:\n  - path: a.py\n    region: r1\n"
                "    blob: " + "1" * 40 + "\n    hash: nothex\n---\nb\n")
        got = self.probs(text)
        self.assertTrue(any("malformed hash" in g for g in got), got)

    def test_hash_without_region(self):
        text = "---\nid: c\nsources:\n  - path: a.py\n    hash: " + "2" * 40 + "\n---\nb\n"
        got = self.probs(text)
        self.assertTrue(any("has a hash but no region" in g for g in got), got)

    def test_region_with_blob_but_no_hash(self):
        text = ("---\nid: c\nsources:\n  - path: a.py\n    region: r1\n"
                "    blob: " + "1" * 40 + "\n---\nb\n")
        got = self.probs(text)
        self.assertIn("source 'a.py#r1' must pin both blob and hash", got)

    def test_region_with_hash_but_no_blob(self):
        text = ("---\nid: c\nsources:\n  - path: a.py\n    region: r1\n"
                "    hash: " + "2" * 40 + "\n---\nb\n")
        got = self.probs(text)
        self.assertIn("source 'a.py#r1' must pin both blob and hash", got)

    def test_duplicate_path_and_region(self):
        text = ("---\nid: c\nsources:\n"
                "  - path: a.py\n    region: r1\n"
                "  - path: a.py\n    region: r1\n---\nb\n")
        got = self.probs(text)
        self.assertIn("source 'a.py#r1' is listed twice", got)

    def test_same_path_whole_and_region_is_valid(self):
        text = ("---\nid: c\nsources:\n"
                "  - path: a.py\n"
                "  - path: a.py\n    region: r1\n---\nb\n")
        got = self.probs(text)
        self.assertEqual([g for g in got if "listed twice" in g], [])

    def test_unknown_key_still_reported(self):
        text = "---\nid: c\nsources:\n  - path: a.py\n    color: red\n---\nb\n"
        got = self.probs(text)
        self.assertTrue(any("unknown keys" in g for g in got), got)


class PlainDirectoryVerifyAndCheck(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        write(self.root, "claims/c.md", region_claim_text("c", "a.py", "r1"))

    def test_verify_output_and_written_fields(self):
        rc, out, err = run_cli(self.root, "verify", "c")
        self.assertEqual(rc, 0, out + err)
        blob = blob_of_bytes((self.root / "a.py").read_bytes())
        h = region_hash("x\ny\n")
        self.assertIn(f"  a.py#r1 @ {h[:12]}", out)
        text = (self.root / "claims" / "c.md").read_text()
        self.assertIn(f"  - path: a.py\n    region: r1\n    blob: {blob}\n    hash: {h}\n", text)
        expected_digest = pin_digest([Source("a.py", blob, "r1", h)])
        self.assertIn(f"pins: {expected_digest}\n", text)

    def test_check_fresh_after_verify(self):
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 0, out)

    def test_edit_outside_region_stays_fresh(self):
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        write(self.root, "a.py", "before, now longer\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 0, out)
        rc, out, _ = run_cli(self.root, "check", "--json")
        data = json.loads(out)
        self.assertEqual(data["results"][0]["state"], "fresh")

    def test_edit_inside_region_goes_stale(self):
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\nCHANGED\ny\n# claimlock:end r1\nafter\n")
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn("STALE", out)
        self.assertIn("a.py#r1: stale", out)

    def test_removing_end_marker_goes_missing(self):
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\nafter\n")
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn("MISSING", out)

    def test_verify_on_missing_region_refuses_and_leaves_file_unchanged(self):
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\nafter\n")
        before = (self.root / "claims" / "c.md").read_text()
        rc, out, err = run_cli(self.root, "verify", "c")
        self.assertEqual(rc, 1)
        self.assertIn("c: source a.py#r1: region 'r1' has no end marker", err)
        self.assertEqual((self.root / "claims" / "c.md").read_text(), before)


class PinDigestRegions(unittest.TestCase):
    def test_region_entry_changes_the_digest(self):
        whole = pin_digest([Source("a.py", "1" * 40)])
        region = pin_digest([Source("a.py", "1" * 40, "r1", "2" * 40)])
        self.assertNotEqual(whole, region)

    def test_order_independent_with_region_entries(self):
        a = [Source("a.py", "1" * 40, "r1", "2" * 40), Source("b.py", "3" * 40)]
        b = [Source("b.py", "3" * 40), Source("a.py", "1" * 40, "r1", "2" * 40)]
        self.assertEqual(pin_digest(a), pin_digest(b))

    def test_whole_file_digest_matches_the_pre_regions_shape(self):
        blob = "4" * 40
        expected = hashlib.sha1(json.dumps([["a.py", blob]]).encode("ascii")).hexdigest()
        self.assertEqual(pin_digest([Source("a.py", blob)]), expected)


class RegionCache(TmpCase):
    def test_cached_region_hash_is_reused_without_reading_the_file(self):
        p = write(self.tmp, "a.py", "no markers here\n")
        # The file has no "r1" markers at all; a real read would raise
        # RegionError. A hit therefore proves the cache — not the file — was
        # consulted.
        os.utime(p, ns=(OLD, OLD))
        st = p.stat()
        h = Hasher(self.tmp, None, mode="raw")
        h.cache["a.py\0r1"] = [st.st_size, st.st_mtime_ns, "d" * 40, "raw"]
        digest, reason = h.region("a.py", "r1")
        self.assertEqual((digest, reason), ("d" * 40, None))

    def test_use_cache_false_re_reads(self):
        p = write(self.tmp, "a.py", "no markers here\n")
        os.utime(p, ns=(OLD, OLD))
        st = p.stat()
        h = Hasher(self.tmp, None, mode="raw")
        h.cache["a.py\0r1"] = [st.st_size, st.st_mtime_ns, "d" * 40, "raw"]
        digest, reason = h.region("a.py", "r1", use_cache=False)
        self.assertIsNone(digest)
        self.assertEqual(reason, "region 'r1' not found")


if __name__ == "__main__":
    unittest.main()
