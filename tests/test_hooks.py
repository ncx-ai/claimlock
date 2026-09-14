import json
import shutil
import subprocess
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


class HookCase(TmpCase):
    def setUp(self):
        super().setUp()
        self.data = self.tmp / "plugin-data"

    def hook(self, root, event, session="s1", stdin=None):
        payload = stdin if stdin is not None else json.dumps({"session_id": session, "cwd": str(root)})
        rc, out, err = run_cli(root, "hook", event, stdin=payload,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(self.data)})
        self.assertEqual(rc, 0, f"hooks must always exit 0; stderr={err}")
        return json.loads(out) if out.strip() else None

    def store(self, use_git=False):
        root = make_repo(self.tmp / "r", use_git=use_git)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", area="api", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        if use_git:
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "init")
        return root


class Contract(HookCase):
    def test_inert_without_a_store(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        for event in ("session-start", "stop", "post-tool-use"):
            self.assertIsNone(self.hook(plain, event))
        self.assertIsNone(self.hook(plain, "stop", stdin="not json"))
        self.assertFalse(self.data.exists())

    def test_internal_errors_are_logged_not_shown(self):
        # A distinct dir from HookCase.store()'s hardcoded "r": this test also
        # calls self.store() below, and colliding on "r" made the second
        # make_repo's `(root / "claims").mkdir()` raise FileExistsError —
        # a test bug, not a hooks.py bug.
        root = make_repo(self.tmp / "bad", use_git=False, config="bogus = 1\n")
        self.assertIsNone(self.hook(root, "session-start"))
        self.assertIn("unknown key", (self.data / "hook-errors.log").read_text())
        good = self.store()
        self.assertIsNone(self.hook(good, "no-such-event"))
        self.assertIn("unknown hook event", (self.data / "hook-errors.log").read_text())


class SessionStart(HookCase):
    def test_reports_counts_and_areas_to_claude(self):
        root = self.store()
        out = self.hook(root, "session-start")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("1 claims, all fresh", ctx)
        write(root, "a.py", "two!\n")
        ctx = self.hook(root, "session-start", session="s2")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("1 stale", ctx)
        self.assertIn("api (1)", ctx)
        self.assertNotIn("decision", json.dumps(out))
        self.assertTrue((self.data / "sessions" / "s2.json").is_file())

    def test_output_is_bounded(self):
        root = make_repo(self.tmp / "big", use_git=False)
        for i in range(400):
            write(root, f"claims/claim-{i:03d}-with-a-long-identifier.md",
                  claim_text(f"claim-{i:03d}-with-a-long-identifier", status="maybe", area=f"area-{i}"))
        ctx = self.hook(root, "session-start")["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 2000)
        self.assertIn("400 invalid", ctx)


class Stop(HookCase):
    def test_warns_user_only_about_new_problems_once(self):
        root = self.store()
        write(root, "claims/old.md", claim_text("old", status="maybe"))  # pre-existing problem
        self.hook(root, "session-start")
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "two!\n")
        out = self.hook(root, "stop")
        self.assertEqual(set(out), {"systemMessage"})
        self.assertIn("introduced 1 stale (c)", out["systemMessage"])
        self.assertNotIn("old", out["systemMessage"])
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "one\n")
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "three\n")
        self.assertIn("introduced 1 stale (c)", self.hook(root, "stop")["systemMessage"])

    def test_no_baseline_means_no_warning(self):
        root = self.store()
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "stop", session="fresh-session"))
        self.assertIsNone(self.hook(root, "stop", session="fresh-session"))


@NEED_GIT
class HeadMovement(HookCase):
    def test_commit_by_any_means_is_reported_to_claude(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        self.assertIsNone(self.hook(root, "post-tool-use"))
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "post-tool-use"))  # an uncommitted edit is Stop's business
        write(root, "ship.sh", "#!/bin/sh\ngit add -A && git commit -q -m ship\n")
        subprocess.run(["sh", "ship.sh"], cwd=root, check=True, capture_output=True)
        out = self.hook(root, "post-tool-use")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("HEAD moved", ctx)
        self.assertIn("c (stale)", ctx)
        self.assertIsNone(self.hook(root, "post-tool-use"))

    def test_reset_backwards_is_reported(self):
        # Claims are tracked, so a reset restores a claim together with its
        # source. To get drift from a backwards move, commit 1 must hold a pin
        # that only matches commit 2's content.
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "two!\n")
        write(root, "claims/c.md", claim_text("c", area="api", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)  # pinned to "two!"
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c1: pin ahead of content")
        write(root, "a.py", "two!\n")
        git(root, "commit", "-qam", "c2: content catches up")
        self.assertEqual(run_cli(root, "check")[0], 0)
        self.hook(root, "session-start")
        git(root, "reset", "-q", "--hard", "HEAD~1")
        ctx = self.hook(root, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("c (stale)", ctx)

    def test_dangling_marker_committed(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        write(root, "docs/x.md", "Claim: `ghost`\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "docs")
        ctx = self.hook(root, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("docs/x.md:ghost", ctx)

    def test_stop_also_catches_commits_made_outside_tools(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")
        git(root, "commit", "-qam", "from another terminal")
        msg = self.hook(root, "stop")["systemMessage"]
        self.assertIn("HEAD moved", msg)


class NotGit(HookCase):
    def test_post_tool_use_is_silent_outside_git(self):
        root = self.store(use_git=False)
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "post-tool-use"))


if __name__ == "__main__":
    unittest.main()
