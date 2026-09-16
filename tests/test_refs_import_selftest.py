import io
import shutil
import unittest
from unittest import mock

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write
from claimlock import refs, selftest
from claimlock.project import load


class RefLineNumbers(TmpCase):
    def test_a_form_feed_does_not_shift_marker_line_numbers(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "docs/ff.md", "page one\x0cstill line one\nClaim: `a`\n")
        markers, _ = refs.scan(load(root))
        self.assertEqual([(m.path, m.line, m.id) for m in markers], [("docs/ff.md", 2, "a")])


class Refs(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "claims/a.md", claim_text("a", body="Mentions Claim: `zzz` in a claim body."))
        write(self.root, "README.md", "Holds. Claim: `a`\n")
        write(self.root, "docs/x.md", "text\nClaim: `ghost` and Claim: `a`\n")
        write(self.root, ".hidden/y.md", "Claim: `ghost2`\n")
        write(self.root, "node_modules/pkg/z.md", "Claim: `ghost3`\n")
        write(self.root, "notes.txt", "Claim: `ghost4`\n")

    def test_scan_scope_and_census(self):
        markers, files = refs.scan(load(self.root))
        self.assertEqual(files, 2)
        self.assertEqual([(m.path, m.line, m.id) for m in markers],
                         [("README.md", 1, "a"), ("docs/x.md", 2, "ghost"), ("docs/x.md", 2, "a")])

    def test_only_limits_to_given_paths(self):
        markers, files = refs.scan(load(self.root), only={"docs/x.md", "notes.txt", "missing.md"})
        self.assertEqual((files, [m.id for m in markers]), (1, ["ghost", "a"]))

    def test_cli_reports_dangling(self):
        rc, out, _ = run_cli(self.root, "refs")
        self.assertEqual(rc, 1)
        self.assertIn("DANGLING docs/x.md:2  Claim `ghost` names no claim", out)
        self.assertIn("claimlock: 3 markers in 2 files scanned, 1 dangling", out)
        (self.root / "docs/x.md").write_text("Claim: `a`\n")
        rc, out, _ = run_cli(self.root, "refs")
        self.assertEqual(rc, 0)
        self.assertIn("2 markers in 2 files scanned, 0 dangling", out)

    def test_custom_globs_and_pattern(self):
        root = make_repo(self.tmp / "c", use_git=False,
                         config='marker_globs = ["*.txt"]\nmarker_pattern = "see claim ([a-z-]+)"\n')
        write(root, "n.txt", "see claim nope\n")
        write(root, "n.md", "see claim nope\n")
        markers, files = refs.scan(load(root))
        self.assertEqual((files, [m.id for m in markers]), (1, ["nope"]))

    def test_only_rejects_paths_outside_the_root(self):
        outside = write(self.tmp, "outside/secret.md", "Claim: `zzz`\n")
        self.assertTrue(outside.is_file())
        markers, files = refs.scan(load(self.root), only={str(outside)})
        self.assertEqual((files, markers), (0, []))

    def test_only_rejects_dotdot_escape(self):
        # self.root is <tmp>/r; "a/../../x.md" resolves to <tmp>/x.md, outside root.
        escape_target = write(self.tmp, "x.md", "Claim: `zzz`\n")
        self.assertTrue(escape_target.is_file())
        markers, files = refs.scan(load(self.root), only={"a/../../x.md"})
        self.assertEqual((files, markers), (0, []))


class Orphans(TmpCase):
    """Claims that no prose cites — the mirror of a dangling marker
    (docs/specs/2026-09-16-claimlock-orphans-evidence-design.md §3.1)."""

    def test_census_reports_uncited_with_exit_0(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "claims/a.md", claim_text("a"))
        write(root, "claims/b.md", claim_text("b"))
        write(root, "README.md", "Holds. Claim: `a`\n")
        rc, out, _ = run_cli(root, "refs")
        self.assertEqual(rc, 0, out)
        self.assertIn("claimlock: 1 markers in 1 files scanned, 0 dangling, 1 uncited", out)

    def test_orphans_flag_lists_the_uncited_claim_only(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "claims/a.md", claim_text("a"))
        write(root, "claims/b.md", claim_text("b"))
        write(root, "README.md", "Holds. Claim: `a`\n")
        rc, out, _ = run_cli(root, "refs", "--orphans")
        self.assertEqual(rc, 0, out)
        self.assertIn("UNCITED b", out)
        self.assertNotIn("UNCITED a", out)

    def test_dangling_marker_still_exits_1_and_census_reports_both(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "claims/a.md", claim_text("a"))
        write(root, "claims/b.md", claim_text("b"))
        write(root, "README.md", "Holds. Claim: `a` and Claim: `ghost`\n")
        rc, out, _ = run_cli(root, "refs")
        self.assertEqual(rc, 1, out)
        self.assertIn("DANGLING README.md:1  Claim `ghost` names no claim", out)
        self.assertIn("claimlock: 2 markers in 1 files scanned, 1 dangling, 1 uncited", out)

    def test_orphans_capped_at_20_and_full_uncaps(self):
        root = make_repo(self.tmp / "r", use_git=False)
        for i in range(25):
            write(root, f"claims/c{i:02d}.md", claim_text(f"c{i:02d}"))
        rc, out, _ = run_cli(root, "refs", "--orphans")
        self.assertEqual(rc, 0, out)
        listed = [line for line in out.splitlines() if line.startswith("UNCITED")]
        self.assertEqual(len(listed), 20)
        self.assertIn("… and 5 more uncited claims — claimlock refs --orphans --full", out)
        rc, out, _ = run_cli(root, "refs", "--orphans", "--full")
        self.assertEqual(rc, 0, out)
        listed = [line for line in out.splitlines() if line.startswith("UNCITED")]
        self.assertEqual(len(listed), 25)


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class RefsInGit(TmpCase):
    """I4: inside a git work tree, candidates come from git, so ignored trees
    (target/, build/, vendor/…) are never walked or scanned."""

    def test_gitignored_files_are_not_scanned(self):
        from helpers import git
        root = make_repo(self.tmp / "g", use_git=True)
        write(root, ".gitignore", ".claimlock/\ntarget/\n")
        write(root, "docs/tracked.md", "Claim: `ghost-tracked`\n")
        write(root, "docs/deleted.md", "Claim: `ghost-deleted`\n")
        write(root, ".github/hidden.md", "Claim: `ghost-hidden`\n")
        write(root, "node_modules/pkg/r.md", "Claim: `ghost-nm`\n")
        write(root, "claims/c.md", claim_text("c", body="Mentions Claim: `ghost-claims`."))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        (root / "docs/deleted.md").unlink()  # tracked, gone from the work tree
        write(root, "untracked.md", "Claim: `ghost-untracked`\n")
        write(root, "target/doc/ignored.md", "Claim: `ghost-ignored`\n")
        markers, files = refs.scan(load(root))
        self.assertEqual(sorted(m.id for m in markers), ["ghost-tracked", "ghost-untracked"])
        self.assertEqual(files, 2)
        rc, out, _ = run_cli(root, "refs")
        self.assertNotIn("ghost-ignored", out)
        self.assertIn("2 markers in 2 files scanned, 2 dangling", out)

    def test_store_in_a_subdirectory_of_the_repository(self):
        from helpers import git
        top = self.tmp / "top"
        top.mkdir()
        git(top, "init", "-q")
        write(top, "outside.md", "Claim: `ghost-outside`\n")
        root = make_repo(top / "sub", use_git=False)
        write(root, "in.md", "Claim: `ghost-in`\n")
        markers, files = refs.scan(load(root))
        self.assertEqual(([(m.path, m.id) for m in markers], files), ([("in.md", "ghost-in")], 1))

    def test_falls_back_to_walking_when_git_fails(self):
        root = make_repo(self.tmp / "f", use_git=True)
        write(root, ".gitignore", "target/\n")
        write(root, "target/ignored.md", "Claim: `ghost-ignored`\n")
        with mock.patch("claimlock.gitio.ls_files", return_value=None, create=True):
            markers, files = refs.scan(load(root))
        self.assertEqual([m.id for m in markers], ["ghost-ignored"])


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class IgnoredRoot(TmpCase):
    def outer(self, ignore):
        outer = self.tmp / "outer"
        outer.mkdir()
        git(outer, "init", "-q", "-b", "main")
        write(outer, ".gitignore", ignore)
        return outer

    def test_store_inside_an_ignored_directory_is_still_scanned(self):
        outer = self.outer("scratch/\n")
        root = make_repo(outer / "scratch" / "proj", use_git=False)
        write(root, "docs/x.md", "Claim: `ghost`\n")
        markers, files = refs.scan(load(root))
        self.assertEqual((files, [m.id for m in markers]), (1, ["ghost"]))
        rc, out, _ = run_cli(root, "refs")
        self.assertEqual(rc, 1, out)
        self.assertIn("1 dangling", out)

    def test_a_non_ignored_store_still_honours_gitignore(self):
        outer = self.outer("target/\n")
        root = make_repo(outer / "proj", use_git=False)
        write(root, "target/t.md", "Claim: `in-ignored-tree`\n")
        write(root, "docs/ok.md", "Claim: `in-open-tree`\n")
        markers, _ = refs.scan(load(root))
        self.assertEqual([m.id for m in markers], ["in-open-tree"])


class Affected(TmpCase):
    def test_lists_claims_citing_a_path_from_any_cwd(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/a.py", "x\n")
        write(root, "claims/c.md", claim_text("c", sources=("src/a.py",)))
        write(root, "claims/d.md", claim_text("d", sources=("src/b.py",)))
        rc, out, _ = run_cli(root, "affected", "src/a.py")
        self.assertEqual((rc, out.split("\t")[0]), (0, "c"))
        rc, out, _ = run_cli(root / "src", "affected", "a.py")
        self.assertEqual(out.split("\t")[0], "c")
        rc, out, _ = run_cli(root, "affected", "nothing.py")
        self.assertEqual((rc, out), (0, ""))


ORIGIN = """---
id: {id}
area: api
status: verified
verified_at: 2026-09-08T10:00:00-04:00
evidence:
  - kind: test
    ref: api::tests::timeout_is_clamped
sources:
  - src/engine.py
  - src/limits.py
---
The per-attempt timeout is clamped to the configured maximum.
"""


ORIGIN_BLANK_SOURCES = """---
id: {id}
area: api
status: verified
verified_at: 2026-09-08T10:00:00-04:00
evidence:
  - kind: test
    ref: api::tests::timeout_is_clamped
sources:
  - src/engine.py

  - src/limits.py
---
The per-attempt timeout is clamped to the configured maximum.
"""


class Import(TmpCase):
    def test_imports_as_unpinned_and_keeps_everything_else(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/engine.py", "e\n")
        write(root, "src/limits.py", "l\n")
        old = self.tmp / "old"
        write(old, "timeout-clamped.md", ORIGIN.format(id="timeout-clamped"))
        write(old, "broken.md", "---\nid: broken\nsources: [x]\n---\nb\n")
        write(old, "README.md", "# old store\n")
        write(root, "claims/exists.md", claim_text("exists"))
        write(old, "exists.md", ORIGIN.format(id="exists"))
        rc, out, err = run_cli(root, "import", str(old))
        self.assertEqual(rc, 1)  # errors were reported
        self.assertIn("imported 1 claim", out)
        self.assertIn("broken.md:3", err)
        self.assertIn("exists.md", err)
        text = (root / "claims/timeout-clamped.md").read_text()
        self.assertIn("sources:\n  - path: src/engine.py\n  - path: src/limits.py\n---", text)
        self.assertIn("ref: api::tests::timeout_is_clamped", text)
        self.assertIn("verified_at: 2026-09-08T10:00:00-04:00", text)
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("UNPINNED timeout-clamped", out)

    def test_origin_sources_with_interior_blank_line_is_not_corrupted(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/engine.py", "e\n")
        write(root, "src/limits.py", "l\n")
        old = self.tmp / "old"
        write(old, "timeout-clamped.md", ORIGIN_BLANK_SOURCES.format(id="timeout-clamped"))
        rc, out, err = run_cli(root, "import", str(old))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("imported 1 claim", out)
        text = (root / "claims/timeout-clamped.md").read_text()
        self.assertIn("sources:\n  - path: src/engine.py\n  - path: src/limits.py\n---", text)
        rc, out, _ = run_cli(root, "check")
        self.assertNotIn("listed twice", out)

    def test_dash_c_resolves_src_relative_to_the_dir_flag(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "src/engine.py", "e\n")
        write(root, "src/limits.py", "l\n")
        write(root, "legacy/timeout-clamped.md", ORIGIN.format(id="timeout-clamped"))
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        rc, out, err = run_cli(elsewhere, "-C", str(root), "import", "legacy")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("imported 1 claim", out)
        self.assertTrue((root / "claims/timeout-clamped.md").exists())

    def test_missing_src_is_exit_2_with_no_traceback(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, out, err = run_cli(root, "import", "nope")
        self.assertEqual(rc, 2)
        self.assertIn(str((root / "nope")), err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")

    def test_src_that_is_a_file_not_a_directory_is_exit_2(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "notadir.md", "x\n")
        rc, out, err = run_cli(root, "import", "notadir.md")
        self.assertEqual(rc, 2)
        self.assertIn(str((root / "notadir.md")), err)


class SelfTest(TmpCase):
    def test_passes_on_a_working_build(self):
        buf = io.StringIO()
        rc = selftest.run(out=lambda s="": buf.write(s + "\n"))
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("edited source: expected 'stale', got 'stale'", buf.getvalue())
        if shutil.which("git"):
            self.assertIn("[git repository]", buf.getvalue())

    def test_fails_when_staleness_detection_is_broken(self):
        buf = io.StringIO()
        with mock.patch("claimlock.selftest.C.freshness", return_value=("fresh", [])):
            rc = selftest.run(out=lambda s="": buf.write(s + "\n"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", buf.getvalue())

    def test_cli(self):
        rc, out, _ = run_cli(self.tmp, "self-test")
        self.assertEqual(rc, 0, out)
        self.assertIn("SELF-TEST: all", out)


if __name__ == "__main__":
    unittest.main()
