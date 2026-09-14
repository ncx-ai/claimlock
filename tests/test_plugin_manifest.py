import json
import os
import unittest

from helpers import REPO


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


if __name__ == "__main__":
    unittest.main()
