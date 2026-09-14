import re
import subprocess
import sys
import unittest

from helpers import BIN, REPO

SKILLS = ["using-claimlock", "operating-claimlock", "evidence-standards", "design-lenses"]
FORBIDDEN = re.compile(r"boogy|foundationdb|\bfdb\b|wasm|verify\.sh|scripts/truth|docs/truth", re.I)


class SkillFormat(unittest.TestCase):
    def test_frontmatter_and_generic_content(self):
        for name in SKILLS:
            with self.subTest(name):
                text = (REPO / "skills" / name / "SKILL.md").read_text()
                m = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", text)
                self.assertIsNotNone(m, "frontmatter must be exactly name + description")
                self.assertEqual(m.group(1), name)
                self.assertTrue(m.group(2).startswith("Use when"), m.group(2))
                self.assertLessEqual(len(m.group(2)), 500)
                self.assertIsNone(FORBIDDEN.search(text), FORBIDDEN.search(text))

    def test_every_command_a_skill_names_exists(self):
        help_text = subprocess.run([sys.executable, str(BIN), "--help"], capture_output=True, text=True).stdout
        commands = set(re.findall(r"^\s{4}([a-z-]+)\s", help_text, re.M))
        self.assertIn("check", commands)
        for name in SKILLS:
            text = (REPO / "skills" / name / "SKILL.md").read_text()
            # only code contexts (backticks or 4-space indented lines), so prose
            # like "when claimlock reports" is not mistaken for a command
            for cmd in re.findall(r"(?:`|^\s{4})claimlock ([a-z][a-z-]+)", text, re.M):
                with self.subTest(skill=name, command=cmd):
                    self.assertIn(cmd, commands)
