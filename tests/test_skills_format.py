import re
import subprocess
import sys
import unittest

from helpers import BIN, REPO

SKILLS = ["using-claimlock", "operating-claimlock", "evidence-standards", "design-lenses"]
# Skills that teach the CLI and so must name at least one command the test can check.
CLAIMLOCK_SKILLS = ["using-claimlock", "operating-claimlock"]
GATE = {"self-test", "check", "refs"}
FORBIDDEN = re.compile(
    r"boogy|foundationdb|\bfdb\b|wasm|verify\.sh|scripts/truth|docs/truth"
    r"|relational-semantics|handoff-\d{4}|docs/superpowers|guarantee-audit",
    re.I,
)


def code_spans(text):
    """Code contexts only — inline backtick spans and 4-space-indented lines — so
    prose like "when claimlock reports" is not mistaken for a command."""
    return re.findall(r"`+([^`\n]+?)`+", text) + re.findall(r"^ {4}(.*)$", text, re.M)


def named_commands(text):
    """Every `claimlock <cmd>` anywhere on a code line, including after `&&`."""
    return {cmd for span in code_spans(text)
            for cmd in re.findall(r"(?<![\w-])claimlock ([a-z][a-z-]*)", span)}


def skill_text(name):
    return (REPO / "skills" / name / "SKILL.md").read_text()


class SkillFormat(unittest.TestCase):
    def test_frontmatter_and_generic_content(self):
        for name in SKILLS:
            with self.subTest(name):
                text = skill_text(name)
                m = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", text)
                self.assertIsNotNone(m, "frontmatter must be exactly name + description")
                self.assertEqual(m.group(1), name)
                self.assertTrue(m.group(2).startswith("Use when"), m.group(2))
                self.assertLessEqual(len(m.group(2)), 500)
                self.assertIsNone(FORBIDDEN.search(text), FORBIDDEN.search(text))

    def test_extractor_sees_every_command_on_a_code_line(self):
        self.assertEqual(named_commands("    claimlock self-test && claimlock check && claimlock refs\n"), GATE)
        self.assertEqual(named_commands("run `cd x && claimlock diff a-b` now"), {"diff"})
        self.assertEqual(named_commands("when claimlock reports a stale claim"), set())

    def test_every_command_a_skill_names_exists(self):
        help_text = subprocess.run([sys.executable, str(BIN), "--help"], capture_output=True, text=True).stdout
        commands = set(re.findall(r"^\s{4}([a-z-]+)\s", help_text, re.M))
        self.assertIn("check", commands)
        for name in SKILLS:
            named = named_commands(skill_text(name))
            if name in CLAIMLOCK_SKILLS:
                with self.subTest(skill=name, checked="non-empty"):
                    self.assertTrue(named, f"{name} names no checkable claimlock command")
            for cmd in sorted(named):
                with self.subTest(skill=name, command=cmd):
                    self.assertIn(cmd, commands)

    def test_gate_line_commands_are_all_checked(self):
        self.assertLessEqual(GATE, named_commands(skill_text("operating-claimlock")))
