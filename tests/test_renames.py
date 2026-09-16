import json
import os
import re
import shutil
import unittest
import unittest.mock as mock

from helpers import TmpCase, claim_text, git, make_repo, pinned_text, run_cli, write

from claimlock import gitio
from claimlock.claims import Source, pin_digest
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


def region_claim_text(cid, path, region, status="unverified", area="core"):
    """A claim citing one region source, block-style (matches
    tests/test_regions.py's helper of the same shape)."""
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c",
             "sources:", f"  - path: {path}", f"    region: {region}",
             "---", "Holds.", ""]
    return "\n".join(lines)


def two_region_claim_text(cid, path1, path2, region, status="unverified", area="core"):
    """A claim citing two region sources of the same region name, on
    different files — for the two-renamed-onto-one-target collision test."""
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c",
             "sources:",
             f"  - path: {path1}", f"    region: {region}",
             f"  - path: {path2}", f"    region: {region}",
             "---", "Holds.", ""]
    return "\n".join(lines)


MALFORMED_PINS_TEXT = "\n".join([
    "---", "id: c", "area: core", "status: verified",
    "evidence:", "  - kind: test", "    ref: s::c",
    "sources:", "  - path: a.py",
    "pins: not-a-real-digest",
    "---", "Holds.", "",
])


CONFLICTED_TEXT = "\n".join([
    "---", "id: c", "area: core", "status: verified",
    "evidence:", "  - kind: test", "    ref: s::c",
    "sources:", "  - path: a.py",
    "<<<<<<< HEAD", "    blob: " + "a" * 40,
    "=======", "    blob: " + "b" * 40,
    ">>>>>>> other", "---", "Holds.", "",
])


@NEED_GIT
class FindRenames(TmpCase):
    """Unit tests for gitio.find_renames against real repos (spec §3.1)."""

    def test_committed_pure_move(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        git(root, "commit", "-qm", "rename")
        sha = git(root, "rev-parse", "--short=7", "HEAD").strip()
        self.assertEqual(gitio.find_renames(root, ["a.py"]), {"a.py": ("b.py", sha)})

    def test_rename_with_edit_is_found(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\ntwo\nthree\nfour\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        write(root, "b.py", "one\ntwo\nthree\nCHANGED\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "rename+edit")
        sha = git(root, "rev-parse", "--short=7", "HEAD").strip()
        self.assertEqual(gitio.find_renames(root, ["a.py"]), {"a.py": ("b.py", sha)})

    def test_chain_of_two_renames_reports_end_to_end(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        git(root, "commit", "-qm", "rename1")
        sha1 = git(root, "rev-parse", "--short=7", "HEAD").strip()
        git(root, "mv", "b.py", "c.py")
        git(root, "commit", "-qm", "rename2")
        self.assertEqual(gitio.find_renames(root, ["a.py"]), {"a.py": ("c.py", sha1)})

    def test_staged_uncommitted_move(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        self.assertEqual(gitio.find_renames(root, ["a.py"]), {"a.py": ("b.py", "uncommitted")})

    def test_plain_unstaged_move_is_invisible(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        os.rename(root / "a.py", root / "b.py")
        self.assertEqual(gitio.find_renames(root, ["a.py"]), {})

    def test_plain_deletion_is_not_a_rename(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        (root / "a.py").unlink()
        git(root, "add", "-A")
        git(root, "commit", "-qm", "delete")
        self.assertEqual(gitio.find_renames(root, ["a.py"]), {})

    def test_outside_git(self):
        root = self.tmp / "plain"
        root.mkdir()
        write(root, "a.py", "one\n")
        self.assertEqual(gitio.find_renames(root, ["a.py"]), {})


@NEED_GIT
class CheckDiffStaleRenamed(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=True)
        write(self.root, "a.py", "one\n")
        write(self.root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(self.root, "verify", "c")[0], 0)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        (self.root / "lib").mkdir()
        git(self.root, "mv", "a.py", "lib/a.py")
        git(self.root, "commit", "-qm", "rename")
        self.sha = git(self.root, "rev-parse", "--short=7", "HEAD").strip()

    def test_check_reports_renamed(self):
        rc, out, err = run_cli(self.root, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("RENAMED  c", out)
        self.assertIn(f"         a.py: renamed → lib/a.py ({self.sha})", out)
        # The hint prints once, in the hints: block, not per-claim (spec §3.1).
        self.assertIn("hints:", out)
        self.assertIn("  renamed: a source was renamed — run: claimlock follow c", out)
        self.assertIn(", 0 missing, 1 renamed", out)

    def test_check_json(self):
        rc, out, err = run_cli(self.root, "check", "--json")
        self.assertEqual(rc, 1, out + err)
        data = json.loads(out)
        [r] = [x for x in data["results"] if x["id"] == "c"]
        self.assertEqual(r["state"], "renamed")
        [s] = r["sources"]
        self.assertEqual(s["state"], "renamed")
        self.assertEqual(s["renamed_to"], "lib/a.py")

    def test_stale_lists_renamed(self):
        rc, out, err = run_cli(self.root, "stale")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("c\tcore\trenamed\ta.py", out)

    def test_diff_shows_the_rename(self):
        rc, out, err = run_cli(self.root, "diff", "c")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"--- a.py: renamed to lib/a.py in {self.sha} — run: claimlock follow c", out)


@NEED_GIT
class Follow(TmpCase):
    def test_follow_pure_move_reads_fresh(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        (root / "lib").mkdir()
        git(root, "mv", "a.py", "lib/a.py")
        git(root, "commit", "-qm", "rename")
        blob = blob_of_bytes(b"one\n")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "followed c: a.py → lib/a.py (fresh)\n")
        text = (root / "claims/c.md").read_text()
        self.assertIn(f"  - path: lib/a.py\n    blob: {blob}\n", text)
        self.assertNotIn("path: a.py\n", text)
        expected_pins = pin_digest([Source("lib/a.py", blob)])
        self.assertIn(f"pins: {expected_pins}\n", text)
        self.assertEqual(run_cli(root, "check")[0], 0)

    def test_follow_rename_with_edit_reads_stale(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\ntwo\nthree\nfour\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        write(root, "b.py", "one\ntwo\nthree\nCHANGED\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "rename+edit")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "followed c: a.py → b.py (stale)\n")

    def test_follow_region_source_keeps_region_and_hash(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "before\n# claimlock:begin r1\nx\ny\n# claimlock:end r1\nafter\n")
        write(root, "claims/c.md", region_claim_text("c", "a.py", "r1"))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        (root / "lib").mkdir()
        git(root, "mv", "a.py", "lib/a.py")
        git(root, "commit", "-qm", "rename")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "followed c: a.py#r1 → lib/a.py#r1 (fresh)\n")
        text = (root / "claims/c.md").read_text()
        self.assertIn("  - path: lib/a.py\n", text)
        self.assertIn("    region: r1\n", text)
        self.assertIn("    hash: ", text)

    def test_digest_mismatch_stays_unchanged(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        text = (root / "claims/c.md").read_text()
        wrong = "0" * 40
        self.assertRegex(text, r"pins: [0-9a-f]{40}\n")
        text = re.sub(r"pins: [0-9a-f]{40}\n", f"pins: {wrong}\n", text)
        (root / "claims/c.md").write_text(text)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        git(root, "commit", "-qm", "rename")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 0, err)
        new_text = (root / "claims/c.md").read_text()
        self.assertIn(f"pins: {wrong}\n", new_text)

    def test_digest_absent_stays_absent(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        blob = blob_of_bytes(b"one\n")
        write(root, "claims/c.md", pinned_text("c", [("a.py", blob)]))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        git(root, "commit", "-qm", "rename")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 0, err)
        new_text = (root / "claims/c.md").read_text()
        self.assertNotIn("pins:", new_text)


@NEED_GIT
class FollowRefusals(TmpCase):
    def test_no_renamed_sources(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        before = (root / "claims/c.md").read_text()
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1)
        self.assertIn("c: no renamed sources", err)
        self.assertEqual((root / "claims/c.md").read_text(), before)

    def test_new_path_already_cited(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        blob_a = blob_of_bytes(b"one\n")
        write(root, "claims/c.md", pinned_text("c", [("a.py", blob_a), ("lib/a.py", None)]))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        before = (root / "claims/c.md").read_text()
        (root / "lib").mkdir()
        git(root, "mv", "a.py", "lib/a.py")
        git(root, "commit", "-qm", "rename")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1)
        self.assertIn("c: lib/a.py is already cited", err)
        self.assertEqual((root / "claims/c.md").read_text(), before)

    def test_two_whole_file_sources_renamed_onto_the_same_target_are_refused(self):
        # Two independent renames (each detected via its OWN deleting commit's
        # diff to HEAD) can legitimately resolve to the same new path: build
        # it so a.py's own diff pairs with c.py, and b.py's own (separate,
        # non-overlapping) diff independently pairs with c.py too — verified
        # empirically (probe, 2026-09-15) that git's -M pairing picks a.py
        # over b.py in the first diff (leaving b.py a plain D there) and
        # cleanly pairs b.py alone in the second (a.py is already long gone
        # by that diff's own start point).
        root = make_repo(self.tmp / "r", use_git=True)
        content = "one\ntwo\nthree\nfour\n"
        write(root, "a.py", content)
        write(root, "b.py", content)
        write(root, "claims/c.md", claim_text("c", sources=("a.py", "b.py")))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "C1")
        git(root, "mv", "a.py", "c.py")
        git(root, "commit", "-qm", "C2")
        git(root, "rm", "-q", "c.py")
        git(root, "commit", "-qm", "C3")
        git(root, "mv", "b.py", "c.py")
        git(root, "commit", "-qm", "C4")
        sha_a = git(root, "log", "-1", "--format=%H", "--diff-filter=D", "--", "a.py").strip()[:7]
        sha_b = git(root, "log", "-1", "--format=%H", "--diff-filter=D", "--", "b.py").strip()[:7]
        self.assertEqual(gitio.find_renames(root, ["a.py", "b.py"]),
                         {"a.py": ("c.py", sha_a), "b.py": ("c.py", sha_b)},
                         "precondition: both a.py and b.py must independently trace to c.py")
        before = (root / "claims/c.md").read_text()
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("c: c.py is already cited", err)
        self.assertEqual((root / "claims/c.md").read_text(), before)

    def test_two_region_sources_renamed_onto_the_same_target_are_refused(self):
        root = make_repo(self.tmp / "r", use_git=True)
        content = "# claimlock:begin r1\nx\ny\n# claimlock:end r1\n"
        write(root, "a.py", content)
        write(root, "b.py", content)
        write(root, "claims/c.md", two_region_claim_text("c", "a.py", "b.py", "r1"))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "C1")
        git(root, "mv", "a.py", "c.py")
        git(root, "commit", "-qm", "C2")
        git(root, "rm", "-q", "c.py")
        git(root, "commit", "-qm", "C3")
        git(root, "mv", "b.py", "c.py")
        git(root, "commit", "-qm", "C4")
        before = (root / "claims/c.md").read_text()
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("c: c.py is already cited", err)
        self.assertEqual((root / "claims/c.md").read_text(), before)

    def test_malformed_pins_is_refused(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", MALFORMED_PINS_TEXT)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1)
        self.assertIn("c has problems that must be fixed first (run: claimlock check)", err)
        self.assertEqual((root / "claims/c.md").read_text(), MALFORMED_PINS_TEXT)

    def test_conflicted_claim(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", CONFLICTED_TEXT)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1)
        self.assertEqual((root / "claims/c.md").read_text(), CONFLICTED_TEXT)

    def test_outside_git(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1)
        self.assertIn("follow needs a git repository", err)

    def test_unknown_id(self):
        root = make_repo(self.tmp / "r", use_git=True)
        rc, out, err = run_cli(root, "follow", "x")
        self.assertEqual(rc, 1)
        self.assertIn("no claim 'x'", err)


@NEED_GIT
class FollowOwed(TmpCase):
    def test_owed_claim_reads_renamed_and_follow_works(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        git(root, "commit", "-qm", "rename")
        self.assertEqual(run_cli(root, "owe", "c", "--to", "bob@example.com")[0], 0)
        rc, out, err = run_cli(root, "check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("(renamed)", out)
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 0, err)
        self.assertIn("followed c: a.py → b.py", out)


@NEED_GIT
class HooksRenamed(TmpCase):
    def test_session_start_counts_renamed(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        git(root, "commit", "-qm", "rename")
        data = self.tmp / "plugin-data"
        payload = json.dumps({"session_id": "s1", "cwd": str(root)})
        rc, out, err = run_cli(root, "hook", "session-start", stdin=payload,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(data)})
        self.assertEqual(rc, 0, err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("1 renamed", ctx)


@NEED_GIT
class HooksCommonPathNoGit(TmpCase):
    def test_post_tool_use_with_head_unchanged_invokes_no_git(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        data = self.tmp / "plugin-data"
        payload = json.dumps({"session_id": "s1", "cwd": str(root)})
        rc, out, err = run_cli(root, "hook", "session-start", stdin=payload,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(data)})
        self.assertEqual(rc, 0, err)
        fake = self.tmp / "fakebin"
        fake.mkdir()
        calls = self.tmp / "git-calls"
        (fake / "git").write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\nexit 1\n")
        os.chmod(fake / "git", 0o755)
        rc, out, err = run_cli(root, "hook", "post-tool-use", stdin=payload,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(data),
                                    "PATH": str(fake)})
        self.assertEqual((rc, out), (0, ""), err)
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")


@NEED_GIT
class FinalFixRenames(TmpCase):
    """Final-review findings I1, I2, M2, M4 (rename detection and follow)."""

    def _deleted_then_restored(self):
        """a.txt committed, deleted in a commit, then restored in a later one —
        so a commit that deleted it exists, yet it is present in HEAD."""
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.txt", "alpha\nbeta\ngamma\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.txt",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "rm", "-q", "a.txt")
        git(root, "commit", "-qm", "delete")
        write(root, "a.txt", "alpha\nbeta\ngamma\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "restore")
        return root

    def test_i1_plain_rm_of_a_restored_path_is_not_a_rename(self):
        root = self._deleted_then_restored()
        write(root, "b.txt", "alpha\nbeta\ngamma\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "identical copy elsewhere")
        (root / "a.txt").unlink()
        self.assertEqual(gitio.find_renames(root, ["a.txt"]), {})
        rc, out, err = run_cli(root, "check", "--json")
        self.assertEqual(json.loads(out)["results"][0]["state"], "missing", out + err)

    def test_i1_staged_mv_of_a_restored_path_is_uncommitted(self):
        root = self._deleted_then_restored()
        git(root, "mv", "a.txt", "b.txt")
        self.assertEqual(gitio.find_renames(root, ["a.txt"]), {"a.txt": ("b.txt", "uncommitted")})

    def test_i2_glob_metacharacters_in_the_path_are_literal(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "src/[id].tsx", "export const page = 1;\nexport default page;\n")
        write(root, "src/i.tsx", "something entirely different\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "src/[id].tsx", "src/[slug].tsx")
        git(root, "commit", "-qm", "rename")
        sha = git(root, "rev-parse", "--short=7", "HEAD").strip()
        git(root, "rm", "-q", "src/i.tsx")
        git(root, "commit", "-qm", "delete i")
        self.assertEqual(gitio.find_renames(root, ["src/[id].tsx"]),
                         {"src/[id].tsx": ("src/[slug].tsx", sha)})

    def test_m2_one_tree_diff_per_deleting_commit_and_one_staged_diff(self):
        root = make_repo(self.tmp / "r", use_git=True)
        for name in ("a", "b", "x", "y"):
            write(root, f"{name}.py", f"{name} content line\n" * 3)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "a2.py")
        git(root, "mv", "b.py", "b2.py")
        git(root, "commit", "-qm", "rename a and b")
        sha = git(root, "rev-parse", "--short=7", "HEAD").strip()
        git(root, "mv", "x.py", "x2.py")
        git(root, "mv", "y.py", "y2.py")
        calls, real = [], gitio.run

        def recording(r, *args):
            calls.append(args)
            return real(r, *args)

        with mock.patch.object(gitio, "run", recording):
            got = gitio.find_renames(root, ["a.py", "b.py", "x.py", "y.py"])
        self.assertEqual(got, {"a.py": ("a2.py", sha), "b.py": ("b2.py", sha),
                               "x.py": ("x2.py", "uncommitted"), "y.py": ("y2.py", "uncommitted")})
        diffs = [a for a in calls if "diff" in a]
        self.assertEqual(len([a for a in diffs if "--cached" in a]), 1, diffs)
        self.assertEqual(len([a for a in diffs if "--cached" not in a]), 1, diffs)

    def test_m4_follow_refuses_a_claim_with_problems(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        text = region_claim_text("c", "a.py", "r1").replace("    region: r1", '    region: "a #b"')
        write(root, "claims/c.md", text)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        git(root, "commit", "-qm", "rename")
        rc, out, err = run_cli(root, "follow", "c")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("c has problems that must be fixed first (run: claimlock check)", err)
        self.assertEqual((root / "claims/c.md").read_text(), text)


if __name__ == "__main__":
    unittest.main()
