import json
import os
import re
import shutil
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")
NO_IDENTITY = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


@NEED_GIT
class ScopedGate(TmpCase):
    def setUp(self):
        super().setUp()
        r = self.root = make_repo(self.tmp / "r", use_git=True)
        write(r, ".gitignore", ".claimlock/\n")
        write(r, "src1.py", "one\n")
        write(r, "src2.py", "two\n")
        write(r, "claims/c1.md", claim_text("c1", sources=("src1.py",)))
        write(r, "claims/c2.md", claim_text("c2", sources=("src2.py",)))
        run_cli(r, "verify", "c1", "c2")
        git(r, "add", "-A")
        git(r, "commit", "-qm", "base")
        write(r, "src2.py", "TWO\n")           # drift already on main, never re-verified
        git(r, "commit", "-qam", "drift on main")
        git(r, "checkout", "-q", "-b", "feature")

    def gate(self, *extra):
        return run_cli(self.root, "check", "--changed", "main", *extra)

    def test_a_change_to_a_cited_source_blocks_and_old_drift_is_listed(self):
        write(self.root, "src1.py", "ONE\n")
        git(self.root, "commit", "-qam", "change src1")
        rc, out, err = self.gate()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("STALE    c1", out)
        self.assertIn("pre-existing (not changed here):", out)
        self.assertIn("  c2: stale", out)
        self.assertIn("claimlock: 2 claims (1 in scope)", out)

    def test_an_unrelated_change_does_not_block_on_old_drift(self):
        write(self.root, "other.txt", "x\n")
        git(self.root, "add", "other.txt")
        git(self.root, "commit", "-qm", "unrelated")
        rc, out, _ = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertIn("  c2: stale", out)
        self.assertIn("(0 in scope)", out)
        self.assertEqual(run_cli(self.root, "check")[0], 1, "plain check stays strict")

    def test_an_owed_claim_does_not_block(self):
        write(self.root, "src1.py", "ONE\n")
        git(self.root, "commit", "-qam", "change src1")
        self.assertEqual(run_cli(self.root, "owe", "c1", "--to", "bob@example.com")[0], 0)
        git(self.root, "commit", "-qam", "hand off c1")
        rc, out, _ = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"OWED     c1 → bob@example\.com since [0-9a-f]{7}, 1 commit ago")

    def test_a_claim_committed_with_an_uncommitted_pin_blocks(self):
        write(self.root, "src1.py", "ONE\n")
        run_cli(self.root, "verify", "c1")
        git(self.root, "add", "claims/c1.md")   # the source edit stays uncommitted and unstaged
        git(self.root, "commit", "-qm", "claim only")
        rc, out, _ = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("UNANCHORED c1", out)

    def test_json_marks_scope_and_blocking(self):
        write(self.root, "src1.py", "ONE\n")
        git(self.root, "commit", "-qam", "change src1")
        rc, out, _ = self.gate("--json")
        data = json.loads(out)
        by_id = {r["id"]: r for r in data["results"]}
        self.assertEqual(data["scope"], ["c1"])
        self.assertEqual((by_id["c1"]["in_scope"], by_id["c1"]["blocking"]), (True, True))
        self.assertEqual((by_id["c2"]["in_scope"], by_id["c2"]["blocking"]), (False, False))

    def test_bad_base_is_exit_2(self):
        rc, _, err = run_cli(self.root, "check", "--changed", "no-such-ref")
        self.assertEqual(rc, 2)
        self.assertIn("merge base", err)


class GateOutsideGit(TmpCase):
    def test_changed_needs_git(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, _, err = run_cli(root, "check", "--changed", "main")
        self.assertEqual(rc, 2)
        self.assertIn("git repository", err)


@NEED_GIT
class WhoVerified(TmpCase):
    def test_verifier_survives_later_commits_by_someone_else(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        run_cli(root, "verify", "c")
        rc, out, _ = run_cli(root, "who", "c")
        self.assertEqual(out, "a.py\tuncommitted\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        git(root, "-c", "user.email=other@example.com", "commit", "-q", "--allow-empty", "-m", "unrelated")
        rc, out, _ = run_cli(root, "who", "c")
        self.assertRegex(out, r"^a\.py\tt@example\.com\t\d{4}-\d\d-\d\dT[^\t]+\t[0-9a-f]{7}\n$")
        rc, out, _ = run_cli(root, "show", "c")
        self.assertIn("verified by t@example.com at", out)


@NEED_GIT
class WhoAttribution(TmpCase):
    """`gitio.verifier` must survive a rename of the claim file and must not
    be fooled by a claim body that merely mentions the pin's own text."""

    def repo(self):
        root = make_repo(self.tmp / "r", use_git=True)
        git(root, "config", "user.email", "alice@example.com")
        git(root, "config", "user.name", "alice")
        return root

    def test_a_rename_of_the_claim_file_does_not_reassign_attribution(self):
        root = self.repo()
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "alice verifies c")
        # Bob renames only the claim file. The loader requires id == filename,
        # so the frontmatter's id is updated too — the pin lines are untouched.
        git(root, "mv", "claims/c.md", "claims/c-renamed.md")
        p = root / "claims" / "c-renamed.md"
        p.write_text(p.read_text().replace("id: c\n", "id: c-renamed\n"))
        git(root, "add", "-A")
        git(root, "-c", "user.email=bob@example.com", "commit", "-qm", "bob renames the claim")
        rc, out, _ = run_cli(root, "who", "c-renamed")
        self.assertRegex(out, r"^a\.py\talice@example\.com\t")

    def test_a_prose_mention_of_the_pin_does_not_steal_attribution(self):
        root = self.repo()
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "alice verifies c")
        text = (root / "claims" / "c.md").read_text()
        blob = re.search(r"blob: ([0-9a-f]{40})", text).group(1)
        write(root, "claims/c.md", text + f"\n(See blob: {blob} for details.)\n")
        git(root, "add", "-A")
        git(root, "-c", "user.email=bob@example.com", "commit", "-qm", "bob adds a note citing the pin")
        rc, out, _ = run_cli(root, "who", "c")
        self.assertRegex(out, r"^a\.py\talice@example\.com\t")
        rc, out, _ = run_cli(root, "show", "c")
        self.assertIn("verified by alice@example.com at", out)

    def test_a_genuine_reverify_reassigns_attribution(self):
        root = self.repo()
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "alice verifies c")
        write(root, "a.py", "two\n")
        git(root, "commit", "-qam", "source changes")
        run_cli(root, "verify", "c")
        git(root, "add", "-A")
        git(root, "-c", "user.email=bob@example.com", "commit", "-qm", "bob re-verifies c")
        rc, out, _ = run_cli(root, "who", "c")
        self.assertRegex(out, r"^a\.py\tbob@example\.com\t")


class WhoOutsideGit(TmpCase):
    def test_unknown_without_git_and_unpinned(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "who", "c")[1], "a.py\tunpinned\n")
        run_cli(root, "verify", "c")
        self.assertEqual(run_cli(root, "who", "c")[1], "a.py\tunknown\n")
        self.assertEqual(run_cli(root, "who", "nope")[0], 1)


class OwnerFilters(TmpCase):
    def test_owed_by_and_mine(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        for cid in ("c1", "c2"):
            write(root, f"claims/{cid}.md", claim_text(cid, sources=("a.py",)))
        run_cli(root, "verify", "c1", "c2")
        write(root, "a.py", "two\n")
        run_cli(root, "owe", "c1", "--to", "bob@example.com")
        run_cli(root, "owe", "c2", "--to", "amy@example.com")
        rc, out, _ = run_cli(root, "stale", "--owed-by", "bob@example.com")
        self.assertEqual((rc, out), (0, "c1\tcore\towed\tbob@example.com\n"))
        rc, out, _ = run_cli(root, "list", "--owed-by", "amy@example.com")
        self.assertIn("c2 (core) [owed → amy@example.com]", out)
        self.assertNotIn("c1", out)
        rc, _, err = run_cli(root, "list", "--mine", env=NO_IDENTITY)
        self.assertEqual(rc, 2)
        self.assertIn("user.email", err)


if __name__ == "__main__":
    unittest.main()
