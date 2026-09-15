import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import unittest.mock as mock

from helpers import BIN, TmpCase, claim_text, clone, git, init_bare, make_repo, run_cli, write

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

    def test_errors_with_no_known_data_dir_go_to_the_home_directory(self):
        # M-e: the fallback was a shared temp path; it is now per user.
        home = self.tmp / "home"
        with mock.patch.object(hooks_mod.Path, "home", return_value=home):
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                hooks_mod._log(None, "stop")
            hooks_mod._log_note(None, "a note")
        log = (home / ".claimlock" / "hook-errors.log").read_text()
        self.assertIn("boom", log)
        self.assertIn("a note", log)

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

    @NEED_GIT
    def test_owed_to_you_lead_is_bounded(self):
        # The owed-to-you line comes first and names up to 10 ids; with long
        # ids it alone passes LIMIT, so it must sit inside the truncation.
        root = self.store(use_git=True)
        ids = [f"owed-{'q' * 240}-{i:02d}" for i in range(12)]
        for cid in ids:
            write(root, f"claims/{cid}.md", claim_text(
                cid, status="owed", sources=("a.py",),
                extra_lines=("owed_by: t@example.com", "owed_since: none")))
        ctx = self.hook(root, "session-start")["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(ctx.startswith("claimlock: owed to you: 12 (owed-"), ctx[:80])
        self.assertEqual(len(ctx), 2000)  # proves truncation actually happened


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

    def test_a_conflicted_claim_says_run_resolve(self):
        root = self.store()
        self.hook(root, "session-start")
        write(root, "claims/c.md", "<<<<<<< ours\nid: c\n=======\nid: c\n>>>>>>> theirs\n")
        msg = self.hook(root, "stop")["systemMessage"]
        self.assertIn("1 claim became conflicted (c) — run `claimlock resolve`", msg)

    def test_stop_never_suppresses_a_name_the_message_does_not_show(self):
        # A HEAD report whose last named id ends just short of LIMIT, plus
        # drift for the "since the last check" line, so the two together pass
        # LIMIT: every id the report's `named` removed from that line must
        # still be visible in the final Stop message.
        root = self.store()
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")  # c goes stale: a since-line exists
        for n in range(100, 400):
            hit = [(f"h{i}-{'x' * n}", "stale", "x.py", None) for i in range(10)]
            report = hooks_mod._head_report("a" * 40, "b" * 40, hit, [])
            if max(report.index(x) + len(x) for _, x in report.named) > hooks_mod.LIMIT - 60:
                break
        else:
            self.fail("no report ends near LIMIT")
        with mock.patch("claimlock.hooks.head_check", return_value=report):
            msg = self.hook_inprocess(root, "stop")["systemMessage"]
        self.assertEqual(len(msg), 2000)  # precondition: the parts together were cut
        self.assertEqual([x for _, x in sorted(report.named) if x not in msg], [])

    def test_an_item_beyond_the_listed_five_is_reported_again_until_shown(self):
        # A phrase lists 5 ids and only counts the rest; those were not shown,
        # so the baseline must not absorb them.
        root = self.store()
        ids = [f"s{i}" for i in range(7)]
        for cid in ids:
            write(root, f"claims/{cid}.md", claim_text(cid, sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", *ids)[0], 0)
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")  # c and s0..s6 go stale
        first = self.hook(root, "stop")["systemMessage"]
        self.assertIn("8 claims became stale (c, s0, s1, s2, s3…)", first)
        second = (self.hook(root, "stop") or {}).get("systemMessage", "")
        self.assertIn("3 claims became stale (s4, s5, s6)", second)
        self.assertIsNone(self.hook(root, "stop"))

    @NEED_GIT
    def test_suppression_is_per_kind(self):
        # An owed claim has no freshness state, so one real claim cannot be
        # both owed and stale; the report is patched to name "c" only as owed
        # while c is stale from an uncommitted edit.
        root = self.store(use_git=True)
        self.hook_inprocess(root, "session-start")
        write(root, "a.py", "two!\n")
        report = hooks_mod._head_report("a" * 40, "b" * 40, [], [], ["c"])
        self.assertEqual(report.named, frozenset([("owed", "c")]))
        with mock.patch("claimlock.hooks.head_check", return_value=report):
            msg = self.hook_inprocess(root, "stop")["systemMessage"]
        self.assertIn("from your uncommitted edits, 1 claim became stale (c)", msg)

    def test_a_baseline_from_before_owed_and_conflicted_reports_neither_once(self):
        root = self.store()
        write(root, "claims/o.md", claim_text("o", status="owed", sources=("a.py",),
                                              extra_lines=("owed_by: t@example.com", "owed_since: none")))
        write(root, "claims/k.md", "<<<<<<< ours\nid: k\n=======\nid: k\n>>>>>>> theirs\n")
        self.hook(root, "session-start")
        state = self.data / "sessions" / "s1.json"
        st = json.loads(state.read_text())
        self.assertEqual((st["baseline"].pop("owed"), st["baseline"].pop("conflicted")), (["o"], ["k"]))
        state.write_text(json.dumps(st))
        self.assertIsNone(self.hook(root, "stop"))
        baseline = json.loads(state.read_text())["baseline"]
        self.assertEqual((baseline["owed"], baseline["conflicted"]), (["o"], ["k"]))
        self.assertIsNone(self.hook(root, "stop"))


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
        self.assertIn("from your uncommitted edits, 1 claim became stale (d)", msg)
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


class HeadReportText(unittest.TestCase):
    def test_an_empty_author_email_reads_unknown(self):
        text = hooks_mod._head_report("a" * 40, "b" * 40, [("c", "stale", "x.py", ("abc1234", "", "s"))], [])
        self.assertIn('c (stale): x.py changed by unknown in abc1234 "s"', text)

    def test_subject_and_email_are_capped(self):
        w = ("abc1234", "e" * 100 + "@x.io", "S" * 200)
        text = hooks_mod._head_report("a" * 40, "b" * 40, [("c", "stale", "x.py", w)], [])
        self.assertIn(f'changed by {"e" * 80}… in abc1234 "{"S" * 60}…"', text)

    def test_named_holds_only_what_survives_the_cut(self):
        hit = [(f"id-{i:02d}-{'x' * 300}", "stale", "x.py", None) for i in range(10)]
        text = hooks_mod._head_report("a" * 40, "b" * 40, hit, [], ["owed-one"], ["conf-one"])
        self.assertEqual(len(text), 2000)
        self.assertTrue(text.index("owed-one") < text.index("conf-one") < text.index("id-00"))
        pairs = [("owed", "owed-one"), ("conflicted", "conf-one")] + [("stale", h[0]) for h in hit]
        visible = frozenset(pair for pair in pairs if pair[1] in text)
        self.assertIn(("stale", hit[0][0]), visible)
        self.assertNotIn(("stale", hit[9][0]), visible)
        self.assertEqual(text.named, visible)


@NEED_GIT
class TeamHooks(HookCase):
    def setUp(self):
        super().setUp()
        bare = init_bare(self.tmp / "origin.git")
        self.a = clone(bare, self.tmp / "a", "amy@example.com")
        run_cli(self.a, "init")
        write(self.a, "src.py", "MAX = 1\n")
        write(self.a, "claims/c.md", claim_text("c", sources=("src.py",)))
        run_cli(self.a, "verify", "c")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "c")
        git(self.a, "push", "-q", "origin", "main")
        self.b = clone(bare, self.tmp / "b", "ben@example.com")

    def pull_b(self):
        return subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.b, capture_output=True, text=True)

    def test_a_pull_names_who_changed_a_claims_source(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 9\n")
        git(self.a, "commit", "-qam", "raise MAX")
        git(self.a, "push", "-q", "origin", "main")
        self.pull_b()
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn('c (stale): src.py changed by amy@example.com in', ctx)
        self.assertIn('"raise MAX"', ctx)

    def test_a_hand_off_to_you_is_reported_on_pull_and_at_session_start(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 9\n")
        git(self.a, "commit", "-qam", "raise MAX")
        run_cli(self.a, "owe", "c", "--to", "ben@example.com")
        git(self.a, "commit", "-qam", "owe c to ben")
        git(self.a, "push", "-q", "origin", "main")
        self.pull_b()
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Now owed to you: c", ctx)
        ctx = self.hook(self.b, "session-start", session="s2")["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(ctx.startswith("claimlock: owed to you: 1 (c)"), ctx)

    def test_a_conflicted_merge_says_run_resolve(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 2\n")
        run_cli(self.a, "verify", "c")
        git(self.a, "commit", "-qam", "a")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "src.py", "MAX = 3\n")
        run_cli(self.b, "verify", "c")
        git(self.b, "commit", "-qam", "b")
        self.assertNotEqual(self.pull_b().returncode, 0, "precondition: the merge conflicts")
        # A conflicted merge does not move HEAD; the merge finishes when committed.
        git(self.b, "checkout", "--theirs", "src.py")
        git(self.b, "add", "-A")   # stages the marker-bearing claim too; git refuses to commit unmerged paths
        git(self.b, "commit", "-qm", "merge, claim still conflicted", "--no-verify")
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("run `claimlock resolve`", ctx)

    def test_stop_separates_your_uncommitted_edits(self):
        self.hook(self.b, "session-start")
        write(self.b, "src.py", "MAX = 5\n")
        msg = self.hook(self.b, "stop")["systemMessage"]
        self.assertIn("from your uncommitted edits, 1 claim became stale (c)", msg)
        self.assertNotIn("since the last check", msg)

    def push_a(self):
        git(self.a, "push", "-q", "origin", "main")

    def long_range_with_a_hand_off(self):
        deep = "src/" + "deep-directory/" * 3
        ids = [f"c{i:02d}" for i in range(12)]
        for i, cid in enumerate(ids):
            write(self.a, f"{deep}s{i:02d}.py", "V = 1\n")
            write(self.a, f"claims/{cid}.md", claim_text(cid, sources=(f"{deep}s{i:02d}.py",)))
        self.assertEqual(run_cli(self.a, "verify", *ids)[0], 0)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "twelve claims")
        self.push_a()
        self.assertEqual(self.pull_b().returncode, 0)
        self.hook(self.b, "session-start")
        for i in range(12):
            write(self.a, f"{deep}s{i:02d}.py", "V = 2\n")
        git(self.a, "commit", "-qam", "S" * 90)
        self.assertEqual(run_cli(self.a, "owe", "c00", "--to", "ben@example.com")[0], 0)
        git(self.a, "commit", "-qam", "owe c00 to ben")
        self.push_a()
        self.assertEqual(self.pull_b().returncode, 0)

    def test_a_hand_off_survives_a_long_attribution_list(self):
        self.long_range_with_a_hand_off()
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Now owed to you: c00", ctx)
        self.assertLessEqual(len(ctx), 2000)

    def test_stop_never_hides_a_hand_off(self):
        self.long_range_with_a_hand_off()
        msg = self.hook(self.b, "stop")["systemMessage"]  # HEAD moved within this Stop check
        self.assertLessEqual(len(msg), 2000)
        self.assertTrue("Now owed to you: c00" in msg or "became owed (c00" in msg, msg)

    def test_a_claim_owed_to_someone_else_is_not_owed_to_you(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 9\n")
        git(self.a, "commit", "-qam", "raise MAX")
        self.assertEqual(run_cli(self.a, "owe", "c", "--to", "amy@example.com")[0], 0)
        git(self.a, "commit", "-qam", "owe c to amy")
        self.push_a()
        self.pull_b()
        out = self.hook(self.b, "post-tool-use")
        self.assertNotIn("owed to you", json.dumps(out).lower())
        ctx = self.hook(self.b, "session-start", session="s2")["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("owed to you", ctx)

    def test_an_already_owed_claim_is_not_newly_owed_when_another_file_changes(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 9\n")
        git(self.a, "commit", "-qam", "raise MAX")
        self.assertEqual(run_cli(self.a, "owe", "c", "--to", "ben@example.com")[0], 0)
        git(self.a, "commit", "-qam", "owe c to ben")
        self.push_a()
        self.pull_b()
        self.assertIn("Now owed to you: c", json.dumps(self.hook(self.b, "post-tool-use")))  # precondition
        write(self.a, "other.txt", "unrelated\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "unrelated")
        self.push_a()
        self.pull_b()
        self.assertNotIn("Now owed to you", json.dumps(self.hook(self.b, "post-tool-use")))

    def test_attribution_names_the_newest_commit(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 2\n")
        git(self.a, "commit", "-qam", "first bump")
        write(self.a, "src.py", "MAX = 3\n")
        git(self.a, "commit", "-qam", "second bump")
        newest = git(self.a, "rev-parse", "--short=7", "HEAD").strip()
        self.push_a()
        self.pull_b()
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f'c (stale): src.py changed by amy@example.com in {newest} "second bump"', ctx)
        self.assertNotIn("first bump", ctx)

    def test_attribution_uses_the_mailmap(self):
        self.hook(self.b, "session-start")
        write(self.a, ".mailmap", "Amy <amy.canonical@example.com> <amy@example.com>\n")
        write(self.a, "src.py", "MAX = 9\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "raise MAX")
        self.push_a()
        self.pull_b()
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("src.py changed by amy.canonical@example.com in", ctx)

    def test_stop_during_a_merge_does_not_blame_your_edits(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 9\n")
        write(self.a, "notes.txt", "amy\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "amy")
        self.push_a()
        write(self.b, "notes.txt", "ben\n")
        git(self.b, "add", "-A")
        git(self.b, "commit", "-qm", "ben")
        self.assertNotEqual(self.pull_b().returncode, 0, "precondition: notes.txt conflicts")
        self.assertEqual((self.b / "src.py").read_text(), "MAX = 9\n", "precondition: src.py merged cleanly")
        msg = self.hook(self.b, "stop")["systemMessage"]
        self.assertIn("since the last check, 1 claim became stale (c)", msg)
        self.assertNotIn("from your uncommitted edits", msg)

    def test_stop_during_a_revert_does_not_blame_your_edits(self):
        write(self.b, "src.py", "MAX = 2\n")
        write(self.b, "notes.txt", "one\n")
        git(self.b, "add", "-A")
        git(self.b, "commit", "-qm", "two")
        write(self.b, "src.py", "MAX = 3\n")
        write(self.b, "notes.txt", "two\n")
        self.assertEqual(run_cli(self.b, "verify", "c")[0], 0)
        git(self.b, "add", "-A")
        git(self.b, "commit", "-qm", "three")
        to_revert = git(self.b, "rev-parse", "HEAD").strip()
        write(self.b, "notes.txt", "three\n")
        git(self.b, "commit", "-qam", "notes")
        self.hook(self.b, "session-start")  # c fresh
        r = subprocess.run(["git", "revert", "--no-edit", to_revert], cwd=self.b, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, "precondition: notes.txt conflicts")
        self.assertEqual((self.b / "src.py").read_text(), "MAX = 2\n", "precondition: src.py reverted cleanly")
        self.assertTrue((self.b / ".git" / "REVERT_HEAD").exists(), "precondition: a revert is in progress")
        msg = self.hook(self.b, "stop")["systemMessage"]
        self.assertIn("since the last check, 1 claim became stale (c)", msg)
        self.assertNotIn("from your uncommitted edits", msg)

    def test_stop_carries_forward_drift_it_could_not_show(self):
        deep = "src/" + "deep-directory/" * 3
        ids = [f"c{i:02d}" for i in range(12)]
        for i, cid in enumerate(ids):
            write(self.a, f"{deep}s{i:02d}.py", "V = 1\n")
            write(self.a, f"claims/{cid}.md", claim_text(cid, sources=(f"{deep}s{i:02d}.py",)))
        write(self.a, "mine.py", "M = 1\n")
        write(self.a, "claims/mine.md", claim_text("mine", sources=("mine.py",)))
        self.assertEqual(run_cli(self.a, "verify", *ids, "mine")[0], 0)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "claims")
        self.push_a()
        self.assertEqual(self.pull_b().returncode, 0)
        self.hook(self.b, "session-start")
        write(self.b, "mine.py", "M = 2\n")  # uncommitted
        for i in range(12):
            write(self.a, f"{deep}s{i:02d}.py", "V = 2\n")
        git(self.a, "commit", "-qam", "S" * 90)
        self.push_a()
        self.assertEqual(self.pull_b().returncode, 0)
        first = self.hook(self.b, "stop")["systemMessage"]
        self.assertEqual(len(first), 2000)
        self.assertNotIn("(mine)", first, "precondition: the HEAD report crowds out Stop's own line")
        second = (self.hook(self.b, "stop") or {}).get("systemMessage", "")
        self.assertIn("from your uncommitted edits, 1 claim became stale (mine)", second)
        third = (self.hook(self.b, "stop") or {}).get("systemMessage", "")
        self.assertNotIn("mine", third)


@NEED_GIT
class CommonPathRunsNoGit(HookCase):
    def test_post_tool_use_with_head_unchanged_invokes_no_git(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        fake = self.tmp / "fakebin"
        fake.mkdir()
        calls = self.tmp / "git-calls"
        (fake / "git").write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\nexit 1\n")
        os.chmod(fake / "git", 0o755)
        payload = json.dumps({"session_id": "s1", "cwd": str(root)})
        rc, out, err = run_cli(root, "hook", "post-tool-use", stdin=payload,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(self.data),
                                    "PATH": str(fake)})
        self.assertEqual((rc, out), (0, ""), err)
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")
        self.assertFalse((self.data / "hook-errors.log").exists())


if __name__ == "__main__":
    unittest.main()
