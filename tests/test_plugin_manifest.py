import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from helpers import BIN, REPO

# Patterns for machine-specific paths that must never end up in committed,
# public-bound content (a real acceptance run's absolute checkout path or
# `mktemp -d` scratch directory, pasted verbatim into a doc). Each pattern is
# written so it does NOT match its own definition below — the char class (or
# escape) immediately follows the "trigger" prefix in the source, so this
# very line never flags itself; nothing else needs to be excluded from the
# scan besides this test file (see `_relpath` below).
LEAK_PATTERNS = [
    re.compile(r"/home/[A-Za-z0-9_.-]+"),
    re.compile(r"/Users/[A-Za-z0-9_.-]+"),
    re.compile(r"/tmp/tmp\."),
    re.compile(r"/tmp/claude-\d+"),
]


class Manifest(unittest.TestCase):
    def test_plugin_marketplace_and_hooks_agree(self):
        plugin = json.loads((REPO / ".claude-plugin/plugin.json").read_text())
        market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual(plugin["name"], "claimlock")
        self.assertTrue(plugin["description"].startswith("Claude Code plugin:"))
        [entry] = market["plugins"]
        self.assertEqual((entry["name"], entry["source"]), ("claimlock", "./"))
        self.assertIn("name", market["owner"])
        hooks = json.loads((REPO / "hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "Stop", "PostToolUse"})
        commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
        for event in ("session-start", "stop", "post-tool-use", "post-edit"):
            self.assertTrue(any(c.endswith(f"hook {event}") for c in commands), event)
        self.assertTrue(all("${CLAUDE_PLUGIN_ROOT}/bin/claimlock" in c for c in commands))
        # Two PostToolUse entries, disjoint matchers, neither shadowing the
        # other — pinned so a bad merge silently dropping the post-edit entry
        # (mutation-proven: deleting it left the whole suite green) is caught.
        self.assertEqual(len(hooks["PostToolUse"]), 2)
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "Bash|mcp__.*")
        self.assertEqual(hooks["PostToolUse"][1]["matcher"], "Edit|Write|MultiEdit|NotebookEdit")
        self.assertEqual(hooks["SessionStart"][0]["matcher"], "startup|resume|clear|compact|fork")

    def test_launcher_is_executable(self):
        self.assertTrue(os.access(REPO / "bin/claimlock", os.X_OK))

    def test_readme_documents_install_and_every_command(self):
        import re, subprocess, sys
        from helpers import BIN
        readme = (REPO / "README.md").read_text()
        self.assertIn("/plugin marketplace add", readme)
        self.assertIn("/plugin install claimlock@claimlock", readme)
        help_text = subprocess.run([sys.executable, str(BIN), "--help"], capture_output=True, text=True).stdout
        for cmd in re.findall(r"^\s{4}([a-z-]+)\s", help_text, re.M):
            self.assertIn(f"claimlock {cmd}", readme, cmd)
        self.assertTrue((REPO / "LICENSE").read_text().startswith("MIT License"))
        self.assertTrue((REPO / "docs/format.md").is_file())


OLD_PYTHON = (
    "import runpy, sys\n"
    "sys.version_info = (3, 10, 0, 'final', 0)\n"
    "sys.argv = [sys.argv[1]] + sys.argv[2:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


class LauncherOnOldPython(unittest.TestCase):
    """C1: a too-old interpreter must never turn a hook into a blocking error."""

    def _run(self, *args, env=None):
        import sys, tempfile
        e = dict(os.environ)
        e.pop("CLAUDE_PLUGIN_DATA", None)
        e.update(env or {})
        return subprocess.run([sys.executable, "-c", OLD_PYTHON, str(BIN), *args],
                              capture_output=True, text=True, env=e, timeout=60,
                              cwd=tempfile.gettempdir())

    def test_hook_exits_0_silently_and_logs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            for event in ("stop", "session-start", "post-tool-use"):
                r = self._run("hook", event, env={"CLAUDE_PLUGIN_DATA": str(data)})
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), event)
            log = (data / "hook-errors.log").read_text()
            self.assertEqual(log.count("3.11"), 3)

    def test_hook_log_over_1mib_is_not_appended_to(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            log = data / "hook-errors.log"
            log.write_bytes(b"x" * (1024 * 1024 + 1))
            r = self._run("hook", "stop", env={"CLAUDE_PLUGIN_DATA": str(data)})
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
            self.assertEqual(log.stat().st_size, 1024 * 1024 + 1)

    def test_hook_log_under_1mib_is_still_appended(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            log = data / "hook-errors.log"
            log.write_bytes(b"x" * 100)
            r = self._run("hook", "stop", env={"CLAUDE_PLUGIN_DATA": str(data)})
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
            self.assertGreater(log.stat().st_size, 100)
            self.assertIn("3.11", log.read_bytes()[100:].decode())

    def test_hook_without_plugin_data_still_exits_0(self):
        r = self._run("hook", "stop")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_interactive_command_still_exits_2(self):
        r = self._run("check")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("3.11", r.stderr)

    def test_launcher_parses_on_old_python(self):
        import ast
        ast.parse((REPO / "bin/claimlock").read_text(), feature_version=(3, 6))


class DocTokenFigures(unittest.TestCase):
    """README.md states the approximate token cost of itself, docs/format.md
    and the two skills combined; both skills repeat the docs/format.md and
    README.md figures. They're exact-looking numbers that every doc edit
    invalidates -- they went stale three separate times inside one change
    (2026-09-17) and were only caught by manual re-measurement. This checks
    them against a fresh `len(path.read_bytes()) / 4` instead: each figure is
    rounded to the nearest 500 (bounding the rounding error at 250), so a
    tolerance of 500 catches real drift without firing on the rounding
    itself."""

    TOLERANCE = 500

    def _tokens(self, rel):
        return len((REPO / rel).read_bytes()) / 4

    def _assert_close(self, label, stated, rel):
        measured = self._tokens(rel)
        self.assertLessEqual(abs(stated - measured), self.TOLERANCE,
                              f"{label}: stated ~{stated:g}, measured {measured:.1f} tokens for {rel}")

    def test_readme_states_its_own_skills_and_format_md_token_cost(self):
        text = (REPO / "README.md").read_text()
        m = re.search(r"claimlock skills ~([0-9,]+) tokens", text)
        self.assertIsNotNone(m, "README.md: skills-combined token figure not found")
        skills_stated = float(m.group(1).replace(",", ""))
        m = re.search(r"`README\.md` ~([0-9,]+)", text)
        self.assertIsNotNone(m, "README.md: its own token figure not found")
        readme_stated = float(m.group(1).replace(",", ""))
        m = re.search(r"`docs/format\.md` ~([0-9,]+)", text)
        self.assertIsNotNone(m, "README.md: docs/format.md token figure not found")
        format_stated = float(m.group(1).replace(",", ""))

        combined = (self._tokens("skills/using-claimlock/SKILL.md")
                    + self._tokens("skills/operating-claimlock/SKILL.md"))
        self.assertLessEqual(abs(skills_stated - combined), self.TOLERANCE,
                              f"README.md: stated skills figure ~{skills_stated:g}, "
                              f"measured {combined:.1f} tokens for the two skills combined")
        self._assert_close("README.md's own token figure", readme_stated, "README.md")
        self._assert_close("README.md's docs/format.md figure", format_stated, "docs/format.md")

    def test_skills_state_docs_format_and_readme_token_cost(self):
        for rel in ("skills/using-claimlock/SKILL.md", "skills/operating-claimlock/SKILL.md"):
            text = (REPO / rel).read_text()
            m = re.search(r"~([0-9,]+) and ~([0-9,]+) tokens", text)
            self.assertIsNotNone(m, f"{rel}: token figures not found")
            format_stated = float(m.group(1).replace(",", ""))
            readme_stated = float(m.group(2).replace(",", ""))
            self._assert_close(f"{rel}'s docs/format.md figure", format_stated, "docs/format.md")
            self._assert_close(f"{rel}'s README.md figure", readme_stated, "README.md")


class NoLeakedMachinePaths(unittest.TestCase):
    def test_tracked_files_have_no_machine_specific_paths(self):
        """Every file `git ls-files` reports must be free of this machine's
        absolute paths (home directory, mktemp scratch dirs) — the exact
        thing that leaked into docs/acceptance-*.md and others in review."""
        try:
            r = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                               capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git not available")
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        files = [f for f in r.stdout.decode("utf-8", "replace").split("\0") if f]
        self_rel = Path(__file__).resolve().relative_to(REPO).as_posix()
        findings = []
        for rel in files:
            if rel == self_rel:
                continue  # this file's own detection patterns, by construction (see above)
            p = REPO / rel
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable; not a text file to scan
            for lineno, line in enumerate(text.splitlines(), 1):
                if any(pat.search(line) for pat in LEAK_PATTERNS):
                    findings.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(findings, [],
                          "machine-specific path(s) leaked into tracked files:\n" + "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
