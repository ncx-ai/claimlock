import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import unittest

from helpers import TmpCase, clone, git, init_bare, make_repo, run_cli, write
from claimlock.claims import Source, load_claims, pin_digest, problems
from claimlock.pins import Hasher, blob_of_bytes
from claimlock.project import load
from claimlock.regions import RegionError, extract, region_hash

OLD = time.time_ns() - 100 * 1_000_000_000  # 100 s ago: old enough to cache
NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def region_claim_text(cid, path, region, status="unverified", area="core"):
    """A claim citing one region source, block-style (the frontmatter parser
    has no flow-map syntax)."""
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c",
             "sources:", f"  - path: {path}", f"    region: {region}",
             "---", "Holds.", ""]
    return "\n".join(lines)


def whole_and_region_claim_text(cid, path, region, status="unverified", area="core"):
    """A claim citing both a whole-file source and a region source of the
    same path — the two must resolve/show/diff by key, not collapse."""
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c",
             "sources:", f"  - path: {path}", f"  - path: {path}", f"    region: {region}",
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

    def test_second_complete_pair_is_loud_not_silently_ignored(self):
        data = (b"# claimlock:begin r1\na\n# claimlock:end r1\n"
                b"b\n# claimlock:begin r1\nc\n# claimlock:end r1\n")
        with self.assertRaises(RegionError) as ctx:
            extract(data, "r1")
        self.assertEqual(str(ctx.exception), "region 'r1' begins more than once")

    def test_orphan_trailing_end_after_a_complete_pair(self):
        data = b"# claimlock:begin r1\na\n# claimlock:end r1\nb\n# claimlock:end r1\n"
        with self.assertRaises(RegionError) as ctx:
            extract(data, "r1")
        self.assertEqual(str(ctx.exception), "region 'r1' ends more than once")

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

    def test_malformed_region_not_confused_with_whole_file_duplicate(self):
        # A malformed region name must not fall back to the bare path for
        # duplicate detection — that would wrongly collide it with a
        # legitimate whole-file entry for the same path.
        text = ("---\nid: c\nsources:\n"
                "  - path: a.py\n"
                "  - path: a.py\n    region: Bad_Name\n---\nb\n")
        got = self.probs(text)
        self.assertEqual([g for g in got if "listed twice" in g], [])
        self.assertTrue(any("malformed region name" in g for g in got), got)

    def test_two_identical_malformed_regions_are_still_duplicates(self):
        text = ("---\nid: c\nsources:\n"
                "  - path: a.py\n    region: Bad_Name\n"
                "  - path: a.py\n    region: Bad_Name\n---\nb\n")
        got = self.probs(text)
        self.assertIn("source 'a.py#Bad_Name' is listed twice", got)

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
        # --full: a fresh result is otherwise omitted by default (spec §3.2).
        rc, out, _ = run_cli(self.root, "check", "--json", "--full")
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


@NEED_GIT
class AnchoringFallback(TmpCase):
    """Spec §2.4: a region pin whose blob was never committed/staged is
    anchored anyway when the file's currently staged content still holds the
    same region hash — the fallback only runs once the whole-file blob check
    fails, and only ever reads the STAGED (index stage 0) content."""

    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=True)
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        write(self.root, "claims/c.md", region_claim_text("c", "a.py", "r1"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "initial")

    def _state(self):
        # --full: a fresh result would otherwise be omitted by default (spec §3.2).
        rc, out, err = run_cli(self.root, "check", "--json", "--full")
        self.assertEqual(rc if rc in (0, 1) else 2, rc, out + err)
        return json.loads(out)["results"][0]["state"]

    def test_uncommitted_edit_outside_region_reads_fresh_via_staged_fallback(self):
        write(self.root, "a.py", "before-changed\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        self.assertEqual(self._state(), "fresh")

    def test_control_uncommitted_edit_inside_region_reads_unanchored(self):
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\nCHANGED\ny\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        self.assertEqual(self._state(), "unanchored")

    def test_control_staging_the_edit_reads_fresh(self):
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\nCHANGED\ny\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        git(self.root, "add", "a.py")
        self.assertEqual(self._state(), "fresh")


@NEED_GIT
class DiffRegions(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=True)
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        write(self.root, "claims/c.md", region_claim_text("c", "a.py", "r1"))
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "initial")
        self.pinned_hash = region_hash("x\ny\n")

    def test_stale_region_diff_shows_only_region_lines(self):
        write(self.root, "a.py", "before-changed\n# claimlock:begin r1\nx\nCHANGED\ny\n# claimlock:end r1\nafter\n")
        rc, out, err = run_cli(self.root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"--- a.py#r1 @ {self.pinned_hash[:12]} (verified)", out)
        self.assertIn("+++ a.py#r1 (now)", out)
        self.assertNotIn("before", out)
        self.assertNotIn("after", out)
        self.assertIn("+CHANGED", out)

    def test_removed_end_marker_prints_the_extraction_reason(self):
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\nafter\n")
        rc, out, err = run_cli(self.root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("--- a.py#r1: region 'r1' has no end marker", out)


class ShowRegions(TmpCase):
    def test_show_lists_key_and_hash_pin(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "before\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        write(root, "claims/c.md", region_claim_text("c", "a.py", "r1"))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        h = region_hash("x\ny\n")
        rc, out, err = run_cli(root, "show", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"a.py#r1 — fresh ({h[:12]})", out)


@NEED_GIT
class WhoRegions(TmpCase):
    def test_who_attributes_each_region_by_its_own_hash_line(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py",
              "# claimlock:begin r1\nA\n# claimlock:end r1\n"
              "# claimlock:begin r2\nB\n# claimlock:end r2\n")
        write(root, "claims/c.md", region_claim_text("c", "a.py", "r1"))
        git(root, "config", "user.email", "amy@example.com")
        git(root, "config", "user.name", "amy")
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "verify r1")

        text = (root / "claims" / "c.md").read_text()
        text = text.replace("sources:\n  - path: a.py\n    region: r1\n",
                            "sources:\n  - path: a.py\n    region: r1\n  - path: a.py\n    region: r2\n")
        (root / "claims" / "c.md").write_text(text)
        git(root, "config", "user.email", "ben@example.com")
        git(root, "config", "user.name", "ben")
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "verify r2")

        rc, out, err = run_cli(root, "who", "c")
        self.assertEqual(rc, 0, out + err)
        lines = {l.split("\t")[0]: l for l in out.splitlines()}
        self.assertIn("a.py#r1", lines, out)
        self.assertIn("a.py#r2", lines, out)
        self.assertIn("amy@example.com", lines["a.py#r1"])
        self.assertIn("ben@example.com", lines["a.py#r2"])

        claim_now = (root / "claims" / "c.md").read_text()
        blobs = re.findall(r"    blob: ([0-9a-f]{40})", claim_now)
        hashes = re.findall(r"    hash: ([0-9a-f]{40})", claim_now)
        self.assertEqual(len(blobs), 2)
        self.assertEqual(len(set(blobs)), 1, "a.py never changed — both entries share the same blob")
        self.assertEqual(len(hashes), 2)
        self.assertEqual(len(set(hashes)), 2, "each region's hash line must be unique")


@NEED_GIT
class ResolveRegions(TmpCase):
    def setUp(self):
        super().setUp()
        bare = self.tmp / "origin.git"
        init_bare(bare)
        self.a, self.b = self.tmp / "a", self.tmp / "b"
        clone(bare, self.a, "amy@example.com")
        run_cli(self.a, "init")
        write(self.a, "a.py", "before\n# claimlock:begin r1\nx\n# claimlock:end r1\nafter\n")
        write(self.a, "claims/c.md", region_claim_text("c", "a.py", "r1"))
        self.assertEqual(run_cli(self.a, "verify", "c")[0], 0)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "c")
        git(self.a, "push", "-q", "origin", "main")
        clone(bare, self.b, "ben@example.com")

    def both_verify(self, a_region_line, b_region_line):
        write(self.a, "a.py", f"before\n# claimlock:begin r1\n{a_region_line}\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(self.a, "verify", "c")[0], 0)
        git(self.a, "commit", "-qam", "a")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "a.py", f"before\n# claimlock:begin r1\n{b_region_line}\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(self.b, "verify", "c")[0], 0)
        git(self.b, "commit", "-qam", "b")
        return subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.b,
                              capture_output=True, text=True)

    def test_theirs_region_content_keeps_theirs_pin(self):
        pull = self.both_verify("y", "z")
        self.assertNotEqual(pull.returncode, 0, "precondition: the region hash must conflict")
        git(self.b, "checkout", "--theirs", "a.py")
        rc, out, err = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("KEPT", out)
        git(self.b, "add", "-A")
        rc, out, _ = run_cli(self.b, "check")
        self.assertEqual(rc, 0, out)

    def test_new_region_content_makes_the_claim_owed(self):
        self.both_verify("y", "z")
        write(self.b, "a.py", "before\n# claimlock:begin r1\nnew\n# claimlock:end r1\nafter\n")
        rc, out, err = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("OWED", out)
        text = (self.b / "claims" / "c.md").read_text()
        self.assertIn("status: owed", text)
        self.assertNotIn("<<<<<<<", text)


@NEED_GIT
class ResolveWholeAndRegionOfSamePath(TmpCase):
    def test_resolves_by_key_without_collapsing_the_two_entries(self):
        bare = self.tmp / "origin.git"
        init_bare(bare)
        a, b = self.tmp / "a", self.tmp / "b"
        clone(bare, a, "amy@example.com")
        run_cli(a, "init")
        write(a, "a.py", "before\n# claimlock:begin r1\nX\n# claimlock:end r1\nafter\n")
        write(a, "claims/c.md", whole_and_region_claim_text("c", "a.py", "r1"))
        self.assertEqual(run_cli(a, "verify", "c")[0], 0)
        git(a, "add", "-A")
        git(a, "commit", "-qm", "c")
        git(a, "push", "-q", "origin", "main")
        clone(bare, b, "ben@example.com")

        write(a, "a.py", "before-A\n# claimlock:begin r1\nX\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(a, "verify", "c")[0], 0)
        git(a, "commit", "-qam", "a")
        git(a, "push", "-q", "origin", "main")

        write(b, "a.py", "before-B\n# claimlock:begin r1\nX\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(b, "verify", "c")[0], 0)
        git(b, "commit", "-qam", "b")
        pull = subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=b, capture_output=True, text=True)
        self.assertNotEqual(pull.returncode, 0, "precondition: the whole-file blob must conflict")

        git(b, "checkout", "--theirs", "a.py")
        rc, out, err = run_cli(b, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("KEPT", out)
        text = (b / "claims" / "c.md").read_text()
        self.assertEqual(text.count("- path: a.py"), 2, text)
        self.assertIn("    region: r1\n", text)
        self.assertIn("    hash: ", text)
        git(b, "add", "-A")
        rc, out, _ = run_cli(b, "check")
        self.assertEqual(rc, 0, out)


@NEED_GIT
class FinalFixRegions(TmpCase):
    """Final-review findings I3, M3, M6."""

    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=True)
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        write(self.root, "claims/c.md", region_claim_text("c", "a.py", "r1"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "initial")

    def test_i3_verify_with_unstaged_edit_outside_region_pins_the_staged_blob(self):
        write(self.root, "a.py", "before-changed\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        from claimlock import gitio
        staged = gitio.index_blob(self.root, "a.py")
        self.assertIsNotNone(staged)
        self.assertIn(f"    blob: {staged}\n", (self.root / "claims/c.md").read_text())
        write(self.root, "a.py", "before-changed\n# claimlock:begin r1\nx\nCHANGED\ny\n# claimlock:end r1\nafter\n")
        rc, out, err = run_cli(self.root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("not in git", out)
        self.assertIn("+CHANGED", out)

    def test_i3_control_edit_inside_region_still_pins_the_working_tree(self):
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\nNEW\ny\n# claimlock:end r1\nafter\n")
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        wt = blob_of_bytes((self.root / "a.py").read_bytes())
        self.assertIn(f"    blob: {wt}\n", (self.root / "claims/c.md").read_text())

    def test_m3_diff_on_a_region_entry_with_hash_but_no_blob_does_not_crash(self):
        lines = ["---", "id: c", "area: core", "status: verified",
                 "evidence:", "  - kind: test", "    ref: s::c",
                 "sources:", "  - path: a.py", "    region: r1", f"    hash: {'0' * 40}",
                 "---", "Holds.", ""]
        write(self.root, "claims/c.md", "\n".join(lines))
        rc, out, err = run_cli(self.root, "diff", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("Traceback", err)
        self.assertIn("--- a.py#r1: ", out)
        self.assertIn("no blob pinned", out)

    def test_m6_show_prints_the_region_failure_reason(self):
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        h = region_hash("x\ny\n")
        write(self.root, "a.py", "before\n# claimlock:begin r1\nx\ny\nafter\n")
        rc, out, err = run_cli(self.root, "show", "c")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"  a.py#r1 — missing ({h[:12]}): region 'r1' has no end marker", out)


if __name__ == "__main__":
    unittest.main()
