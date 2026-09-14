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
        for event in ("session-start", "stop", "post-tool-use"):
            self.assertTrue(any(c.endswith(f"hook {event}") for c in commands), event)
        self.assertTrue(all("${CLAUDE_PLUGIN_ROOT}/bin/claimlock" in c for c in commands))
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "Bash|mcp__.*")

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
