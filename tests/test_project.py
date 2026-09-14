import os
import shutil
import unittest

from helpers import TmpCase, git, make_repo, write
from claimlock.project import CONFIG, ConfigError, load, safe_source


class Root(TmpCase):
    def test_config_in_ancestor_wins(self):
        root = make_repo(self.tmp / "r", use_git=False)
        sub = root / "a" / "b"
        sub.mkdir(parents=True)
        p = load(sub)
        self.assertEqual(p.root, root)
        self.assertTrue(p.has_config)
        self.assertEqual(p.claims_dir, root / "claims")
        self.assertEqual(p.state_dir, root / ".claimlock")

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_git_toplevel_when_no_config(self):
        top = self.tmp / "g"
        top.mkdir()
        git(top, "init", "-q")
        sub = top / "x"
        sub.mkdir()
        p = load(sub)
        self.assertEqual(p.root, top)
        self.assertFalse(p.has_config)

    def test_start_dir_when_neither(self):
        d = self.tmp / "plain"
        d.mkdir()
        p = load(d)
        self.assertEqual(p.root, d)
        self.assertFalse(p.has_store())
        (d / "claims").mkdir()
        self.assertTrue(load(d).has_store())

    def test_config_values(self):
        root = make_repo(self.tmp / "r", False,
                         config='claims_dir = "docs/claims"\nmarker_globs = ["*.txt"]\n'
                                'marker_pattern = "See ([a-z-]+)"\n')
        p = load(root)
        self.assertEqual(p.claims_dir, root / "docs" / "claims")
        self.assertEqual(p.marker_globs, ["*.txt"])
        self.assertEqual(p.marker_pattern.pattern, "See ([a-z-]+)")

    def test_config_errors(self):
        cases = [
            ("unknown", 'claim_dir = "x"\n', "unknown key"),
            ("toml", "claims_dir = \n", CONFIG),
            ("type", "claims_dir = 3\n", "claims_dir must be a string"),
            ("globs", 'marker_globs = "*.md"\n', "marker_globs must be a list"),
            ("nogroup", 'marker_pattern = "Claim"\n', "capture group"),
            ("badre", 'marker_pattern = "("\n', "marker_pattern"),
            ("escape", 'claims_dir = "../elsewhere"\n', "escapes"),
            ("root", 'claims_dir = "."\n', "claims_dir must be a subdirectory of the project root"),
            ("root-dotdot", 'claims_dir = "sub/.."\n', "claims_dir must be a subdirectory"),
        ]
        for name, cfg, needle in cases:
            with self.subTest(name):
                root = make_repo(self.tmp / name, False, config=cfg)
                with self.assertRaises(ConfigError) as cm:
                    load(root)
                self.assertIn(needle, str(cm.exception))


class SafeSource(TmpCase):
    def test_accepts_and_rejects(self):
        root = self.tmp / "r"
        root.mkdir()
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, root / "link")
        self.assertEqual(safe_source(root, "src/a.py"), root / "src" / "a.py")
        for bad in ["", "/etc/passwd", "../x", "a/../../x", "a\\b", "C:/x", "link/secret"]:
            with self.subTest(bad):
                self.assertIsNone(safe_source(root, bad))


if __name__ == "__main__":
    unittest.main()
