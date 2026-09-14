import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import unittest.mock as mock

from helpers import BIN, TmpCase, claim_text, git, make_repo, run_cli, write

from claimlock import gitio as gitio_mod
from claimlock import hooks as hooks_mod

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

    def hook_inprocess(self, root, event, session="s1"):
        """Same contract as .hook(), but calls hooks.main() directly in this
        process instead of via subprocess — needed wherever a test must
        monkeypatch a module hooks.py imports (e.g. gitio.head) and observe
        the effect, which a subprocess-based call cannot see."""
        payload = json.dumps({"session_id": session, "cwd": str(root)})
        env = {"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(self.data)}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = hooks_mod.main(event, payload, env)
        self.assertEqual(rc, 0, "hooks must always exit 0")
        out = buf.getvalue()
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

    def test_bare_claims_dir_without_config_is_inert(self):
        # I3: a directory that merely happens to be named claims/ is not a
        # store for hooks. Only .claimlock.toml activates them, and deciding
        # that must not spawn git (PostToolUse runs on every Bash/MCP call).
        root = self.tmp / "bare"
        write(root, "claims/2024-q1.md", "quarterly notes\n")
        write(root, "claims/c.md", claim_text("c", status="maybe"))
        no_git = AssertionError("git spawned while deciding whether hooks are active")
        with mock.patch("claimlock.project._git_toplevel", side_effect=no_git), \
                mock.patch("claimlock.gitio.run", side_effect=no_git):
            for event in ("session-start", "stop", "post-tool-use"):
                self.assertIsNone(self.hook_inprocess(root, event), event)
        for event in ("session-start", "stop", "post-tool-use"):
            self.assertIsNone(self.hook(root, event), event)
        self.assertFalse(self.data.exists())
        self.assertFalse((root / ".claimlock").exists())
        (root / ".claimlock.toml").write_text("")
        ctx = self.hook(root, "session-start")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("2 claims", ctx)

    def test_config_in_an_ancestor_activates_hooks(self):
        root = self.store()
        sub = root / "pkg" / "deep"
        sub.mkdir(parents=True)
        out = self.hook(sub, "session-start")
        self.assertIn("1 claims, all fresh", out["hookSpecificOutput"]["additionalContext"])

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

    def test_malformed_stdin_with_a_store_logs_a_note_but_still_reports_normally(self):
        # Finding D: with no store, malformed stdin degrades to {} silently
        # (test_inert_without_a_store above). But once a store exists, going
        # on to silently share "no-session" state for every such call left
        # nothing logged and no way for an operator to notice. It must still
        # report normally (the note is log-only, never shown to Claude).
        root = self.store()
        out = self.hook(root, "session-start", stdin="not json")
        self.assertIsNotNone(out)
        self.assertIn("additionalContext", json.dumps(out))
        self.assertNotIn("valid JSON", json.dumps(out))
        log = (self.data / "hook-errors.log").read_text()
        self.assertIn("not valid JSON", log)


    @unittest.skipIf(hooks_mod.fcntl is None, "no fcntl: session locking unavailable")
    def test_lock_timeout_skips_and_leaves_a_note(self):
        root = self.store()
        lock_path = self.data / "sessions" / "s1.lock"
        lock_path.parent.mkdir(parents=True)
        with open(lock_path, "a+") as held, mock.patch.object(hooks_mod, "LOCK_TIMEOUT_S", 0.1):
            hooks_mod.fcntl.flock(held, hooks_mod.fcntl.LOCK_EX)
            self.assertIsNone(self.hook_inprocess(root, "session-start"))
        log = (self.data / "hook-errors.log").read_text()
        self.assertIn("session-start", log)
        self.assertIn("session lock", log)


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
        # Finding B: 400 claims with short area names ("area-057" etc.) never
        # gets near 2000 chars, because SessionStart's message never lists
        # claim ids — only counts, plus up to 8 "Affected areas" entries. A
        # test asserting only assertLessEqual(len(ctx), 2000) here cannot
        # fail even with `[:LIMIT]` deleted, so it proves nothing about
        # truncation. Genuinely falsifiable input: 8 claims (sorted first
        # alphabetically, so most_common(8)'s ties pick them — Counter
        # documents ties as "ordered in the order first encountered") with
        # very long area names, which the areas line prints in full. The
        # other 400 short-area claims are kept so "408 invalid" still proves
        # the realistic high-claim-count path is exercised under truncation.
        root = make_repo(self.tmp / "big", use_git=False)
        for i in range(8):
            write(root, f"claims/{i:03d}.md",
                  claim_text(f"long-area-claim-{i}", status="maybe", area=f"area-{'x' * 300}-{i}"))
        for i in range(400):
            write(root, f"claims/claim-{i:03d}-with-a-long-identifier.md",
                  claim_text(f"claim-{i:03d}-with-a-long-identifier", status="maybe", area=f"area-{i}"))
        ctx = self.hook(root, "session-start")["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(len(ctx), 2000)  # proves truncation actually happened
        self.assertIn("408 invalid", ctx)


class Stop(HookCase):
    def test_warns_user_only_about_new_problems_once(self):
        root = self.store()
        write(root, "claims/old.md", claim_text("old", status="maybe"))  # pre-existing problem
        self.hook(root, "session-start")
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "two!\n")
        out = self.hook(root, "stop")
        self.assertEqual(set(out), {"systemMessage"})
        self.assertIn("since the last check, 1 claim became stale (c)", out["systemMessage"])
        self.assertNotIn("old", out["systemMessage"])
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "one\n")
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "three\n")
        self.assertIn("since the last check, 1 claim became stale (c)", self.hook(root, "stop")["systemMessage"])

    def test_no_baseline_means_no_warning(self):
        root = self.store()
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "stop", session="fresh-session"))
        self.assertIsNone(self.hook(root, "stop", session="fresh-session"))

    def test_output_is_bounded(self):
        # Finding B: drive Stop's message past LIMIT with long ids in TWO
        # categories at once (invalid + stale) — each category shows at most
        # 5 ids, so this is the genuinely falsifiable input (a handful of
        # short ids, however many claims exist, would never approach 2000).
        root = make_repo(self.tmp / "stopbig", use_git=False)
        write(root, "a.py", "one\n")
        stale_ids = [f"stale-{'x' * 220}-{i}" for i in range(6)]
        for cid in stale_ids:
            write(root, f"claims/{cid}.md", claim_text(cid, area="s", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", *stale_ids)[0], 0)  # fresh, pinned to "one\n"
        self.hook(root, "session-start")  # baseline: no invalid, no stale yet
        invalid_ids = [f"bad-{'y' * 220}-{i}" for i in range(6)]
        for cid in invalid_ids:
            write(root, f"claims/{cid}.md", claim_text(cid, status="maybe", area="i"))
        write(root, "a.py", "two!\n")  # stales all 6 verified claims
        out = self.hook(root, "stop")
        self.assertEqual(set(out), {"systemMessage"})
        self.assertEqual(len(out["systemMessage"]), 2000)  # proves truncation actually happened


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
        # I6: the HEAD-moved report already names c; it is not repeated in the
        # "since the last check" line, which is dropped when nothing remains.
        self.assertNotIn("since the last check", msg)
        self.assertEqual(msg.count("c (stale)"), 1)

    def test_stop_lists_only_what_the_head_report_did_not_name(self):
        root = self.store(use_git=True)
        write(root, "b.py", "bee\n")
        write(root, "claims/d.md", claim_text("d", area="api", sources=("b.py",)))
        self.assertEqual(run_cli(root, "verify", "d")[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "add d")
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")
        git(root, "commit", "-qam", "pulled in from elsewhere")  # stales c by moving HEAD
        write(root, "b.py", "BEE\n")  # stales d, uncommitted
        msg = self.hook(root, "stop")["systemMessage"]
        self.assertIn("since the last check, 1 claim became stale (d)", msg)
        self.assertIn("c (stale)", msg)
        self.assertNotIn("(c)", msg)
        self.assertNotIn("introduced", msg)

    def test_dangling_marker_list_is_marked_when_truncated(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        write(root, "docs/x.md", "".join(f"Claim: `ghost-{i:02d}`\n" for i in range(12)))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "docs")
        ctx = self.hook(root, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("docs/x.md:ghost-09….", ctx)
        self.assertNotIn("ghost-10", ctx)

    def test_transient_git_failure_does_not_lose_the_head_range(self):
        # Finding C: head_check used to read gitio.head twice per check (once
        # itself, once again inside _init_head) and _init_head unconditionally
        # overwrote last_head with whatever the second read returned. A
        # transient git failure on that second read wiped a known last_head
        # to None, so the NEXT successful check would diff only the newest
        # commit (changed_paths(None, new)) instead of the whole missed
        # range — silently dropping any commit sandwiched in between.
        root = self.store(use_git=True)
        self.hook_inprocess(root, "session-start")
        write(root, "a.py", "two!\n")  # stales "c" once committed
        git(root, "add", "-A")
        git(root, "commit", "-qm", "first")
        real_head = gitio_mod.head
        calls = {"n": 0}

        def flaky_head(root_):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_head(root_)

        with mock.patch("claimlock.gitio.head", side_effect=flaky_head):
            # The transient failure makes this check look like nothing moved.
            self.assertIsNone(self.hook_inprocess(root, "post-tool-use"))
        # A second commit moves HEAD again. The next (unpatched) check must
        # report BOTH commits' changes — proving the failed check above did
        # not clobber the previously known last_head to None, which would
        # otherwise make this diff only the second commit and miss "c".
        write(root, "claims/late.md", claim_text("late", area="x"))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "second")
        out = self.hook_inprocess(root, "post-tool-use")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("c (stale)", ctx)

    def test_output_is_bounded(self):
        # Finding B: drive PostToolUse's message past LIMIT with >10 long ids
        # sharing one source — head_check shows at most 10 "hit" claims, so
        # 12 long ids sourcing the same file (all going stale on one commit)
        # is the genuinely falsifiable input.
        root = make_repo(self.tmp / "ptubig", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\n")
        ids = [f"ptu-{'z' * 175}-{i:02d}" for i in range(12)]
        for cid in ids:
            write(root, f"claims/{cid}.md", claim_text(cid, area="p", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", *ids)[0], 0)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")  # stales all 12
        git(root, "commit", "-qam", "invalidate")
        ctx = self.hook(root, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(len(ctx), 2000)  # proves truncation actually happened


class NotGit(HookCase):
    def test_post_tool_use_is_silent_outside_git(self):
        root = self.store(use_git=False)
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "post-tool-use"))


@NEED_GIT
class Concurrency(HookCase):
    def test_sixteen_concurrent_post_tool_use_serialise_on_one_session(self):
        # Finding A: nothing coordinated hooks of the same session, so N
        # concurrent post-tool-use processes each independently loaded the
        # same stale on-disk state, all saw the same HEAD move, and all
        # reported it — plus a fixed "<sid>.tmp" temp-file name meant two
        # writers could collide mid-write. Sixteen concurrent processes,
        # one real HEAD move, must produce exactly one report.
        root = self.store(use_git=True)
        self.hook(root, "session-start", session="concurrent")
        write(root, "a.py", "two!\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "stale it")
        payload = json.dumps({"session_id": "concurrent", "cwd": str(root)})
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        env["CLAUDE_PROJECT_DIR"] = str(root)
        env["CLAUDE_PLUGIN_DATA"] = str(self.data)
        n = 16
        # ExitStack (not a bare list) so every process's stdin/stdout/stderr
        # pipe is closed on the way out, whatever happens — a Popen with PIPE
        # streams read-to-EOF but never closed otherwise leaks its
        # TextIOWrapper file objects until the next GC cycle notices them.
        with contextlib.ExitStack() as stack:
            procs = [stack.enter_context(subprocess.Popen(
                        [sys.executable, str(BIN), "hook", "post-tool-use"],
                        cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, env=env))
                     for _ in range(n)]
            # Feed every process's stdin before reading any output, so all n are
            # actually running concurrently rather than one at a time.
            for p in procs:
                p.stdin.write(payload)
                p.stdin.close()
            outs = []
            for p in procs:
                out = p.stdout.read()
                err = p.stderr.read()
                rc = p.wait(timeout=30)
                self.assertEqual(rc, 0, f"hooks must always exit 0; stderr={err}")
                outs.append(out)
        moved = [o for o in outs if o.strip() and "HEAD moved" in o]
        self.assertEqual(len(moved), 1, f"expected exactly one HEAD-moved report, got: {moved}")
        self.assertFalse((self.data / "hook-errors.log").exists())


if __name__ == "__main__":
    unittest.main()
