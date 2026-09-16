"""The post-edit hook: names the claims a just-edited file backs.

Spec: docs/specs/2026-09-16-claimlock-edit-notice-design.md §3.
"""
import json
import os
import unittest
import unittest.mock as mock

from helpers import claim_text, make_repo, write
from test_hooks import NEED_GIT, HookCase

from claimlock import hooks as hooks_mod
from claimlock import project as P


def region_claim_text(cid, path, region, status="verified"):
    """A claim citing `path#region` — claim_text has no region support."""
    lines = ["---", f"id: {cid}", "area: core", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c", "sources:",
             f"  - path: {path}", f"    region: {region}",
             "    blob: " + "a" * 40, "    hash: " + "a" * 40,
             "---", "Holds.", ""]
    return "\n".join(lines)


def owed_claim_text(cid, sources):
    return claim_text(cid, status="owed", sources=sources,
                       extra_lines=("owed_by: owner@example.com", "owed_since: none"))


class EditCase(HookCase):
    def payload(self, root, session, **tool_input):
        return json.dumps({"session_id": session, "cwd": str(root), "tool_input": tool_input})


class Silence(EditCase):
    def test_no_store(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        write(plain, "a.py", "one\n")
        out = self.hook(plain, "post-edit",
                        stdin=self.payload(plain, "s1", file_path=str(plain / "a.py")))
        self.assertIsNone(out)
        self.assertFalse(self.data.exists())

    def _store(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "b.py", "two\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        return root

    def test_tool_input_absent(self):
        root = self._store()
        stdin = json.dumps({"session_id": "s1", "cwd": str(root)})
        self.assertIsNone(self.hook(root, "post-edit", stdin=stdin))

    def test_file_path_not_a_string(self):
        root = self._store()
        stdin = json.dumps({"session_id": "s1", "cwd": str(root), "tool_input": {"file_path": 123}})
        self.assertIsNone(self.hook(root, "post-edit", stdin=stdin))

    def test_path_outside_root(self):
        root = self._store()
        outside = self.tmp / "elsewhere"
        write(outside, "x.py", "x\n")
        out = self.hook(root, "post-edit",
                        stdin=self.payload(root, "s1", file_path=str(outside / "x.py")))
        self.assertIsNone(out)

    def test_path_inside_claims_dir(self):
        root = self._store()
        out = self.hook(root, "post-edit",
                        stdin=self.payload(root, "s1", file_path=str(root / "claims" / "c1.md")))
        self.assertIsNone(out)

    def test_unverified_only_citation_is_silent(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c1.md", claim_text("c1", status="unverified", sources=("a.py",)))
        out = self.hook(root, "post-edit",
                        stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIsNone(out)

    def test_uncited_path_is_silent(self):
        root = self._store()
        out = self.hook(root, "post-edit",
                        stdin=self.payload(root, "s1", file_path=str(root / "b.py")))
        self.assertIsNone(out)


class NamesTheClaims(EditCase):
    def test_verified_and_owed_are_both_named(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        write(root, "claims/c2.md", owed_claim_text("c2", ("a.py",)))
        out = self.hook(root, "post-edit",
                        stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIsNotNone(out)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PostToolUse")
        text = hso["additionalContext"]
        for needle in ("a.py", "c1", "c2", "verified", "owed", "claimlock diff", "claimlock verify"):
            self.assertIn(needle, text, text)

    def test_singular_claim_uses_it(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("invalidated it", text, text)
        self.assertNotIn("invalidated them", text, text)

    def test_plural_claims_uses_them(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        write(root, "claims/c2.md", owed_claim_text("c2", ("a.py",)))
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("invalidated them", text, text)
        self.assertNotIn("invalidated it", text, text)

    def test_region_source_is_named_without_hashing(self):
        # The hook does not hash, so a region source is named on any edit to
        # its file, whether or not the region itself was touched.
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\ntwo\nthree\n")
        write(root, "claims/c1.md", region_claim_text("c1", "a.py", "main"))
        out = self.hook(root, "post-edit",
                        stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIsNotNone(out)
        self.assertIn("c1", out["hookSpecificOutput"]["additionalContext"])


class PayloadShapes(EditCase):
    def _store(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "d.py", "two\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        write(root, "claims/c2.md", claim_text("c2", status="verified", sources=("d.py",)))
        return root

    def test_absolute_file_path(self):
        root = self._store()
        out = self.hook(root, "post-edit",
                        stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIn("c1", out["hookSpecificOutput"]["additionalContext"])

    def test_relative_file_path(self):
        root = self._store()
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path="a.py"))
        self.assertIn("c1", out["hookSpecificOutput"]["additionalContext"])

    def test_edits_list_collects_each(self):
        root = self._store()
        stdin = json.dumps({"session_id": "s1", "cwd": str(root), "tool_input": {
            "edits": [{"file_path": str(root / "a.py")}, {"file_path": str(root / "d.py")}]}})
        out = self.hook(root, "post-edit", stdin=stdin)
        self.assertIsNotNone(out)
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("c1", text)
        self.assertIn("c2", text)

    def test_notebook_path(self):
        root = self._store()
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", notebook_path=str(root / "a.py")))
        self.assertIn("c1", out["hookSpecificOutput"]["additionalContext"])


class Suppression(EditCase):
    def _store(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "d.py", "two\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        write(root, "claims/c2.md", claim_text("c2", status="verified", sources=("d.py",)))
        return root

    def test_same_path_twice_in_one_session_is_silent_the_second_time(self):
        root = self._store()
        first = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIsNotNone(first)
        second = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIsNone(second)

    def test_a_different_cited_path_in_the_same_session_still_speaks(self):
        root = self._store()
        self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "d.py")))
        self.assertIsNotNone(out)
        self.assertIn("c2", out["hookSpecificOutput"]["additionalContext"])

    def test_same_path_in_a_different_session_speaks(self):
        root = self._store()
        self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s2", file_path=str(root / "a.py")))
        self.assertIsNotNone(out)


class IndexFreshness(EditCase):
    def test_editing_a_claim_files_content_in_place_is_seen(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "b.py", "two\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        # Build the cache: b.py is not yet cited by anything.
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "b.py")))
        self.assertIsNone(out)
        # Same file COUNT, different CONTENT: a directory mtime would miss this.
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py", "b.py")))
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "b.py")))
        self.assertIsNotNone(out, "the cited-claims index must rebuild on an in-place claim edit")
        self.assertIn("c1", out["hookSpecificOutput"]["additionalContext"])


class Bounds(EditCase):
    def test_nine_citing_claims_names_at_most_five(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        for i in range(9):
            write(root, f"claims/c{i}.md", claim_text(f"claim-{i}", status="verified", sources=("a.py",)))
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(text.count("(verified)"), 5)
        self.assertIn("…", text)

    def test_more_than_three_paths_names_at_most_three(self):
        root = make_repo(self.tmp / "r", use_git=False)
        for i in range(5):
            write(root, f"p{i}.py", "x\n")
            write(root, f"claims/c{i}.md", claim_text(f"claim-{i}", status="verified", sources=(f"p{i}.py",)))
        edits = [{"file_path": str(root / f"p{i}.py")} for i in range(5)]
        stdin = json.dumps({"session_id": "s1", "cwd": str(root), "tool_input": {"edits": edits}})
        out = self.hook(root, "post-edit", stdin=stdin)
        text = out["hookSpecificOutput"]["additionalContext"]
        for i in range(3):
            self.assertIn(f"p{i}.py", text, text)
        for i in range(3, 5):
            self.assertNotIn(f"p{i}.py", text, text)
        self.assertIn("…", text)

    def test_many_long_ids_stay_within_the_limit(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        for i in range(9):
            long_id = f"claim{i:03d}-" + "z" * 450
            write(root, f"claims/c{i}.md", claim_text(long_id, status="verified", sources=("a.py",)))
        out = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(text), 2000)
        # A length cut alone is not evidence the message is usable — it must
        # still carry the call to action, intact, and mark that it cut
        # something (spec: the closer is never the part that gets dropped).
        self.assertIn("claimlock diff", text, text)
        self.assertIn("claimlock verify", text, text)
        self.assertTrue(text.endswith("…"), text)


class RecordsOnlyRenderedPaths(EditCase):
    def test_paths_not_named_in_the_message_still_speak_later(self):
        # Regression: a payload touching 5 cited paths named only the first 3
        # (EDIT_PATH_CAP) but used to RECORD all 5 as notified — permanently
        # silencing the other two for the rest of the session even though
        # nothing ever told the reader about them.
        root = make_repo(self.tmp / "r", use_git=False)
        for i in range(5):
            write(root, f"p{i}.py", "x\n")
            write(root, f"claims/c{i}.md", claim_text(f"claim-{i}", status="verified", sources=(f"p{i}.py",)))
        edits = [{"file_path": str(root / f"p{i}.py")} for i in range(5)]
        stdin = json.dumps({"session_id": "s1", "cwd": str(root), "tool_input": {"edits": edits}})
        out = self.hook(root, "post-edit", stdin=stdin)
        text = out["hookSpecificOutput"]["additionalContext"]
        for i in range(3):
            self.assertIn(f"p{i}.py", text, text)
        for i in range(3, 5):
            self.assertNotIn(f"p{i}.py", text, text)
        for i in range(3, 5):
            later = self.hook(root, "post-edit",
                              stdin=self.payload(root, "s1", file_path=str(root / f"p{i}.py")))
            self.assertIsNotNone(later, f"p{i}.py was recorded as notified though the message never named it")


class SessionStartCarriesSuppressionForward(EditCase):
    def test_session_start_does_not_reset_edited_notified(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "d.py", "two\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        write(root, "claims/c2.md", owed_claim_text("c2", ("d.py",)))
        first = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIsNotNone(first)
        self.hook(root, "session-start", session="s1")
        second = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "a.py")))
        self.assertIsNone(second, "a compaction/clear mid-session must not re-arm already-notified paths")
        # A DIFFERENT cited path, first seen after the SessionStart, must still
        # be labelled correctly — not `(unverified)`, the one status this hook
        # excludes as having nothing to drift (spec §3.1 step 3). Regression
        # for the bug where SessionStart carried the "cited" index forward but
        # not "cited_status", desyncing the cache from its own labels.
        third = self.hook(root, "post-edit", stdin=self.payload(root, "s1", file_path=str(root / "d.py")))
        self.assertIsNotNone(third)
        text = third["hookSpecificOutput"]["additionalContext"]
        self.assertIn("c2 (owed)", text, text)
        self.assertNotIn("c2 (unverified)", text, text)
        # A different session is unaffected either way.
        other = self.hook(root, "post-edit", stdin=self.payload(root, "s2", file_path=str(root / "a.py")))
        self.assertIsNotNone(other)


@NEED_GIT
class NoGit(EditCase):
    def _fake_git(self):
        fake = self.tmp / "fakebin"
        fake.mkdir()
        calls = self.tmp / "git-calls"
        (fake / "git").write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\nexit 1\n")
        os.chmod(fake / "git", 0o755)
        return fake, calls

    def _run(self, root, session, path, fake):
        stdin = self.payload(root, session, file_path=str(path))
        from helpers import run_cli
        rc, out, err = run_cli(root, "hook", "post-edit", stdin=stdin,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(self.data),
                                    "PATH": str(fake)})
        return rc, out, err

    def test_post_edit_invokes_no_git_cited_or_not(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "b.py", "two\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        fake, calls = self._fake_git()
        rc, out, err = self._run(root, "s1", root / "a.py", fake)
        self.assertEqual(rc, 0, err)
        rc2, out2, err2 = self._run(root, "s1", root / "b.py", fake)
        self.assertEqual(rc2, 0, err2)
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")
        self.assertFalse((self.data / "hook-errors.log").exists())


class SavesStateOnlyWhenChanged(EditCase):
    def test_silent_no_hit_call_does_not_rewrite_state(self):
        # post_tool_use already snapshots-before/saves-only-if-different
        # (hooks.py ~line 601); post_edit's silent path did not, rewriting an
        # identical session-state file (measured: 52 KiB) on every no-op call.
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "b.py", "two\n")  # cited by nothing — every call is silent
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        stdin = self.payload(root, "s1", file_path=str(root / "b.py"))
        env = {"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(self.data)}
        # First call builds the citation-index cache: a real, expected state change.
        self.assertEqual(hooks_mod.main("post-edit", stdin, env), 0)
        self.assertTrue((self.data / "sessions" / "s1.json").exists())
        with mock.patch.object(hooks_mod, "_save_state") as saved:
            self.assertEqual(hooks_mod.main("post-edit", stdin, env), 0)
            saved.assert_not_called()


class SignatureIgnoresReadme(EditCase):
    def test_readme_does_not_affect_the_citation_signature(self):
        # load_claims skips claims/README.md; _claims_signature must agree,
        # or creating/editing a README forces a rebuild that cannot change
        # the result it produces.
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c1.md", claim_text("c1", status="verified", sources=("a.py",)))
        project = P.load(root)
        before = hooks_mod._claims_signature(project)
        write(root, "claims/README.md", "# notes\n")
        after = hooks_mod._claims_signature(project)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
