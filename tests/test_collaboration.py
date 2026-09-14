"""Two-clone collaboration: claims/ is committed and shared, so freshness must
survive push/pull/merge exactly the way any other tracked file does. Only
`.claimlock/` (stat cache + local snapshots) is per-clone and gitignored —
these tests are what makes that split load-bearing rather than asserted.

Skipped entirely (not individually) when git is not on PATH.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import TmpCase, claim_text, git, run_cli, write

GIT_AVAILABLE = shutil.which("git") is not None


def _init_bare(path: Path):
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(path)],
                   check=True, capture_output=True)


def _clone(bare: Path, dest: Path):
    subprocess.run(["git", "clone", "-q", str(bare), str(dest)], check=True, capture_output=True)
    git(dest, "config", "user.email", "t@example.com")
    git(dest, "config", "user.name", "t")
    git(dest, "config", "commit.gpgsign", "false")


def _git_allow_fail(root, *args):
    """Like helpers.git, but for a command (e.g. a conflicting pull/push)
    expected to exit non-zero — helpers.git's check=True would raise."""
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def scaffold(tmp: Path):
    """A bare 'origin', clone A with an initial commit pushed (a claimlock
    store, two source files, two unverified claims each citing one), and
    clone B made from that same origin — the common starting point for every
    scenario below."""
    bare = tmp / "origin.git"
    _init_bare(bare)

    a = tmp / "a"
    _clone(bare, a)
    run_cli(a, "init")
    write(a, "src_a.py", "A = 1\n")
    write(a, "src_b.py", "B = 1\n")
    write(a, "claims/claim-a.md", claim_text("claim-a", sources=("src_a.py",)))
    write(a, "claims/claim-b.md", claim_text("claim-b", sources=("src_b.py",)))
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "initial store")
    git(a, "push", "-q", "origin", "main")

    b = tmp / "b"
    _clone(bare, b)
    return bare, a, b


@unittest.skipUnless(GIT_AVAILABLE, "git not on PATH")
class Collaboration(TmpCase):
    def test_a_independent_verifications_merge_without_conflict(self):
        """(a) A and B verify/edit different claims, push/pull, merge -> check is clean."""
        bare, a, b = scaffold(self.tmp)

        rc, out, err = run_cli(a, "verify", "claim-a")
        self.assertEqual(rc, 0, out + err)
        git(a, "commit", "-q", "-am", "verify claim-a")
        git(a, "push", "-q", "origin", "main")

        rc, out, err = run_cli(b, "verify", "claim-b")
        self.assertEqual(rc, 0, out + err)
        git(b, "commit", "-q", "-am", "verify claim-b")
        # B must integrate A's push before it can push its own commit.
        r = _git_allow_fail(b, "pull", "--no-rebase", "-q", "origin", "main")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        git(b, "push", "-q", "origin", "main")

        git(a, "pull", "-q", "--no-rebase", "origin", "main")

        rc, out, err = run_cli(a, "check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("2 claims", out)
        self.assertNotIn("<<<<<<<", (a / "claims" / "claim-a.md").read_text())
        self.assertNotIn("<<<<<<<", (a / "claims" / "claim-b.md").read_text())

        # B converges to the identical, clean state.
        rc, out, err = run_cli(b, "check")
        self.assertEqual(rc, 0, out + err)

    def test_b_sees_stale_after_pulling_as_source_edit(self):
        """(b) A edits a claim's source and pushes; B pulls -> B's check reports STALE."""
        bare, a, b = scaffold(self.tmp)

        run_cli(a, "verify", "claim-a")
        git(a, "commit", "-q", "-am", "verify claim-a")
        git(a, "push", "-q", "origin", "main")

        git(b, "pull", "-q", "--no-rebase", "origin", "main")
        rc, out, err = run_cli(b, "check")
        self.assertEqual(rc, 0, out + err)  # fresh immediately after pulling the pin + matching content

        write(a, "src_a.py", "A = 2\n")
        git(a, "commit", "-q", "-am", "bump src_a for the claim's enforcement site")
        git(a, "push", "-q", "origin", "main")

        git(b, "pull", "-q", "--no-rebase", "origin", "main")
        rc, out, err = run_cli(b, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("STALE", out)
        self.assertIn("claim-a", out)

    def test_c_pin_conflict_resolved_then_reverified(self):
        """(c) Both verify the same claim against different content -> the two commits
        conflict on claim-a.md's blob lines. Taking the side that does NOT match the
        merged file must report STALE, never falsely fresh, before a re-verify."""
        bare, a, b = scaffold(self.tmp)

        # A verifies claim-a against "A = 100" and commits+pushes (only the claim
        # file — src_a.py's edit is deliberately left uncommitted so the two
        # blobs below conflict without also creating a source-file conflict).
        write(a, "src_a.py", "A = 100\n")
        rc, _, err = run_cli(a, "verify", "claim-a")
        self.assertEqual(rc, 0, err)
        git(a, "add", "claims/claim-a.md")
        git(a, "commit", "-q", "-m", "verify claim-a against A's content")
        git(a, "push", "-q", "origin", "main")

        # B, still on the pre-A base, verifies the SAME claim against different content.
        write(b, "src_a.py", "A = 200\n")
        rc, _, err = run_cli(b, "verify", "claim-a")
        self.assertEqual(rc, 0, err)
        git(b, "add", "claims/claim-a.md")
        git(b, "commit", "-q", "-m", "verify claim-a against B's content")

        push = _git_allow_fail(b, "push", "origin", "main")
        self.assertNotEqual(push.returncode, 0)  # rejected: non-fast-forward

        merge = _git_allow_fail(b, "pull", "--no-rebase", "-q", "origin", "main")
        self.assertNotEqual(merge.returncode, 0)  # conflict on claim-a.md's blob line
        self.assertIn("claims/claim-a.md", (merge.stdout + merge.stderr))

        # Resolve by taking A's (incoming/"theirs") side — the side that does
        # NOT match B's actual working-tree content ("A = 200").
        git(b, "checkout", "--theirs", "--", "claims/claim-a.md")
        git(b, "add", "claims/claim-a.md")

        rc, out, err = run_cli(b, "check")
        self.assertEqual(rc, 1, "a mismatched pin taken from the losing merge side "
                                 "must never read as fresh: " + out + err)
        self.assertIn("STALE", out)
        self.assertIn("claim-a", out)

        # Re-check, then re-verify against what is actually on disk now.
        rc, _, err = run_cli(b, "verify", "claim-a")
        self.assertEqual(rc, 0, err)
        git(b, "commit", "-q", "-am", "merge: resolve claim-a conflict, re-verify")
        git(b, "push", "-q", "origin", "main")

        rc, out, err = run_cli(b, "check")
        self.assertEqual(rc, 0, out + err)

    def test_d_pulled_pin_never_committed_diffs_as_unavailable(self):
        """(d) A verifies against uncommitted content, then edits the file again before
        committing claim + file; the verified blob was never committed to git, so no
        clone can recover it — B (after pulling) sees the claim as STALE and `diff`
        reports the pinned content as unavailable."""
        bare, a, b = scaffold(self.tmp)

        write(a, "src_a.py", "A = 5\n")
        rc, _, err = run_cli(a, "verify", "claim-a")  # pins blob-of("A = 5\n"), never committed
        self.assertEqual(rc, 0, err)

        write(a, "src_a.py", "A = 6\n")  # changed again, before the commit below
        git(a, "add", "-A")
        git(a, "commit", "-q", "-m", "verify + a further edit, committed together")
        git(a, "push", "-q", "origin", "main")

        git(b, "pull", "-q", "--no-rebase", "origin", "main")
        # B never ran verify; git never held blob-of("A = 5") either, since it was
        # never committed anywhere — no clone can recover it.
        rc, out, err = run_cli(b, "check")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("STALE", out)
        self.assertIn("claim-a", out)

        rc, out, err = run_cli(b, "diff", "claim-a")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("unavailable", out)
        self.assertIn("is not in git", out)

    def test_e_deleted_claim_leaves_a_dangling_marker_after_merge(self):
        """(e) A deletes a claim a committed `Claim:` marker in B's docs references;
        after the merge, `claimlock refs` exits 1."""
        bare, a, b = scaffold(self.tmp)

        git(a, "rm", "-q", "claims/claim-b.md")
        git(a, "commit", "-q", "-m", "remove claim-b")
        git(a, "push", "-q", "origin", "main")

        write(b, "docs/notes.md", "See Claim: `claim-b` for the retry cap.\n")
        git(b, "add", "-A")
        git(b, "commit", "-q", "-m", "reference claim-b in docs")

        push = _git_allow_fail(b, "push", "origin", "main")
        self.assertNotEqual(push.returncode, 0)  # rejected: non-fast-forward

        merge = _git_allow_fail(b, "pull", "--no-rebase", "-q", "origin", "main")
        self.assertEqual(merge.returncode, 0, merge.stdout + merge.stderr)  # disjoint files: clean auto-merge
        git(b, "push", "-q", "origin", "main")

        self.assertFalse((b / "claims" / "claim-b.md").exists())
        self.assertTrue((b / "docs" / "notes.md").exists())

        rc, out, err = run_cli(b, "refs")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("DANGLING", out)
        self.assertIn("claim-b", out)


if __name__ == "__main__":
    unittest.main()
