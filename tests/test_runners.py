import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from helpers import TmpCase
from claimlock import runners as R


class VitestJson(unittest.TestCase):
    def test_vitest_json_becomes_rel_file_and_name_pairs(self):
        # vitest's JSON gives an ABSOLUTE file and a name WITHOUT the file. Both
        # have to be normalised before comparing, and getting either wrong makes
        # every ref unresolved.
        root = Path("/repo")
        raw = json.dumps([
            {"name": "createShell > attributes a frame", "file": "/repo/packages/web-sdk/src/shell.test.ts"},
            {"name": "boogyDev plugin > serves", "file": "/repo/packages/web-sdk/dev/plugin.test.ts"},
        ])
        self.assertEqual(
            R.parse_vitest_json(raw, root),
            {("packages/web-sdk/src/shell.test.ts", "createShell > attributes a frame"),
             ("packages/web-sdk/dev/plugin.test.ts", "boogyDev plugin > serves")})

    def test_vitest_json_that_is_not_json_yields_nothing_rather_than_raising(self):
        self.assertEqual(R.parse_vitest_json("not json at all", Path("/repo")), set())

    def test_an_entry_of_the_wrong_shape_is_skipped_rather_than_raising(self):
        # A runner's output is not a claim, but it is still input claimlock did
        # not write: a report that is valid JSON of the wrong shape must lose
        # that entry, not the whole answer.
        raw = json.dumps(["a string", {"name": 7, "file": "/repo/a.test.ts"},
                          {"file": "/repo/a.test.ts"},
                          {"name": "kept", "file": "/repo/a.test.ts"}])
        self.assertEqual(R.parse_vitest_json(raw, Path("/repo")), {("a.test.ts", "kept")})

    def test_a_listed_file_outside_the_repo_is_not_claimed(self):
        # The control for the relative_to() arm: without it a linked-in package
        # elsewhere on disk would be reported under a nonsense relative path.
        raw = json.dumps([{"name": "t", "file": "/elsewhere/a.test.ts"},
                          {"name": "u", "file": "/repo/b.test.ts"}])
        self.assertEqual(R.parse_vitest_json(raw, Path("/repo")), {("b.test.ts", "u")})


class PackageRoot(TmpCase):
    def test_the_package_root_is_the_nearest_ancestor_with_vitest(self):
        # A monorepo has a root package.json too; the list must run in the
        # package that actually has vitest, or it lists the wrong project.
        root = self.tmp / "m"
        root.mkdir()
        (root / "package.json").write_text('{"name":"monorepo"}')
        pkg = root / "packages" / "web-sdk"
        (pkg / "src").mkdir(parents=True)
        (pkg / "package.json").write_text('{"devDependencies":{"vitest":"^4"}}')
        (pkg / "src" / "a.test.ts").write_text("")
        self.assertEqual(R.vitest_package_root(root, "packages/web-sdk/src/a.test.ts"), pkg)

    def test_no_ancestor_has_vitest(self):
        root = self.tmp / "n"
        root.mkdir()
        (root / "package.json").write_text('{"name":"monorepo"}')
        (root / "a.test.ts").write_text("")
        self.assertIsNone(R.vitest_package_root(root, "a.test.ts"))

    def test_the_walk_stops_at_the_project_root(self):
        # The falsifier for the loop's termination: a vitest package ABOVE the
        # project root is not this project's, and walking on would leave the
        # tree claimlock was pointed at.
        outer = self.tmp / "o"
        (outer / "inner").mkdir(parents=True)
        (outer / "package.json").write_text('{"devDependencies":{"vitest":"^4"}}')
        (outer / "inner" / "a.test.ts").write_text("")
        self.assertIsNone(R.vitest_package_root(outer / "inner", "a.test.ts"))

    def test_the_cargo_package_root_is_the_nearest_ancestor_with_a_manifest(self):
        root = self.tmp / "c"
        (root / "crates" / "a" / "src").mkdir(parents=True)
        (root / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n')
        (root / "crates" / "a" / "Cargo.toml").write_text('[package]\nname = "a"\n')
        (root / "crates" / "a" / "src" / "lib.rs").write_text("")
        self.assertEqual(R.cargo_package_root(root, "crates/a/src/lib.rs"),
                         root / "crates" / "a")

    def test_no_ancestor_has_a_cargo_manifest(self):
        root = self.tmp / "d"
        root.mkdir()
        (root / "a.rs").write_text("")
        self.assertIsNone(R.cargo_package_root(root, "a.rs"))


class CargoList(unittest.TestCase):
    def test_cargo_list_output_becomes_names(self):
        # `--list` prints "path::to::test: test" lines; anything else is noise.
        out = "db::fdb::ledger::codec_tests::a_case: test\nbenches: benchmark\n"
        self.assertEqual(R.parse_cargo_list(out), {"db::fdb::ledger::codec_tests::a_case"})

    def test_the_summary_line_is_not_a_test_name(self):
        # Measured shape of `cargo test --all-targets -- --list` (verified on a
        # real crate; the "Running unittests …" headers go to stderr): each
        # target's names, a blank line, then "N tests, M benchmarks". The
        # summary holds no ": " at all, so it is dropped by a DIFFERENT guard
        # from the one that drops "benches: benchmark" in the test above — and
        # only that other test covers the `kind` guard, which is why both are
        # here rather than one standing for the other.
        out = ("codec_tests::a_case: test\n"
               "other::nested::deep_one: test\n"
               "\n"
               "3 tests, 0 benchmarks\n"
               "an_integration_test: test\n"
               "\n"
               "1 test, 0 benchmarks\n")
        self.assertEqual(R.parse_cargo_list(out),
                         {"codec_tests::a_case", "other::nested::deep_one", "an_integration_test"})


class RunnerForFile(unittest.TestCase):
    def test_a_runner_is_chosen_by_suffix(self):
        self.assertIs(R.for_file("packages/web-sdk/src/a.test.ts"), R.vitest_tests)
        self.assertIs(R.for_file("packages/web-sdk/src/a.test.tsx"), R.vitest_tests)
        self.assertIs(R.for_file("web/a.test.js"), R.vitest_tests)
        self.assertIs(R.for_file("crates/a/src/lib.rs"), R.cargo_tests)

    def test_a_file_no_runner_knows_chooses_none(self):
        # The control: a Python or Go test file must NOT be handed to vitest.
        self.assertIsNone(R.for_file("tests/test_a.py"))
        self.assertIsNone(R.for_file("a_test.go"))

    def test_for_file_resolves_its_runner_at_call_time(self):
        # Load-bearing for the wiring tests in test_evidence.py, which replace
        # `vitest_tests` on this module: a dispatch table captured at import
        # time would hand back the real subprocess-running function instead.
        with mock.patch.object(R, "vitest_tests", "a stand-in"):
            self.assertEqual(R.for_file("a.test.ts"), "a stand-in")


class Consulting(TmpCase):
    """The subprocess arms. Every argv is fixed and nothing from a ref reaches
    a command line, so these assert the argv itself rather than a runner's
    answer."""

    def _pkg(self, dev='{"devDependencies":{"vitest":"^4"}}'):
        root = self.tmp / "r"
        (root / "src").mkdir(parents=True)
        (root / "package.json").write_text(dev)
        (root / "src" / "a.test.ts").write_text("")
        return root

    def test_the_report_path_handed_to_json_is_never_a_path_from_the_ref(self):
        """The hazard this whole module is written around: `vitest --json` takes
        an OPTIONAL PATH and WRITES the report there. Pointing it at a ref's
        path truncated a real 251-line test file during this feature's
        investigation. Fails if the report path is ever the file asked about,
        anything under the project root, or a relative path."""
        root = self._pkg()
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = list(argv)
            seen["cwd"] = kw.get("cwd")
            Path(argv[argv.index("--json") + 1]).write_text(
                json.dumps([{"name": "a case", "file": str(root / "src" / "a.test.ts")}]))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(R.subprocess, "run", fake_run):
            got = R.vitest_tests(root, "src/a.test.ts")
        self.assertEqual(got, {("src/a.test.ts", "a case")})
        report = Path(seen["argv"][seen["argv"].index("--json") + 1])
        self.assertTrue(report.is_absolute(), report)
        self.assertNotIn("a.test.ts", report.name)
        with self.assertRaises(ValueError):       # not inside the project root
            report.resolve().relative_to(root.resolve())
        self.assertEqual(seen["argv"][:4], ["npx", "vitest", "list", "--json"])
        self.assertEqual(len(seen["argv"]), 5)    # no ref-derived argument
        self.assertEqual(seen["cwd"], root)

    def test_the_report_directory_is_removed_afterwards(self):
        # The report is a temporary file, not an index or a cache: claimlock
        # persists nothing between runs.
        root = self._pkg()
        held = {}

        def fake_run(argv, **kw):
            held["report"] = Path(argv[argv.index("--json") + 1])
            held["report"].write_text("[]")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(R.subprocess, "run", fake_run):
            R.vitest_tests(root, "src/a.test.ts")
        self.assertFalse(held["report"].exists())
        self.assertFalse(held["report"].parent.exists())

    def test_a_missing_toolchain_is_not_a_false_claim(self):
        root = self._pkg()
        with mock.patch.object(R.subprocess, "run", side_effect=FileNotFoundError("npx")):
            self.assertIsNone(R.vitest_tests(root, "src/a.test.ts"))

    def test_a_runner_that_times_out_is_not_a_false_claim(self):
        root = self._pkg()
        with mock.patch.object(R.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("npx", R.TIMEOUT)):
            self.assertIsNone(R.vitest_tests(root, "src/a.test.ts"))

    def test_a_run_that_writes_no_report_is_not_a_false_claim(self):
        # vitest exiting non-zero without writing the file must not read as
        # "this package has no tests", which would fail every ref in it.
        root = self._pkg()
        with mock.patch.object(R.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 1, "", "boom")):
            self.assertIsNone(R.vitest_tests(root, "src/a.test.ts"))

    def test_no_package_root_means_no_subprocess_at_all(self):
        root = self._pkg(dev='{"name":"plain-package"}')
        with mock.patch.object(R.subprocess, "run") as run:
            self.assertIsNone(R.vitest_tests(root, "src/a.test.ts"))
        run.assert_not_called()

    def test_the_cargo_argv_is_fixed_and_runs_in_the_package(self):
        root = self.tmp / "c"
        (root / "crates" / "a" / "src").mkdir(parents=True)
        (root / "Cargo.toml").write_text("[workspace]\n")
        (root / "crates" / "a" / "Cargo.toml").write_text('[package]\nname = "a"\n')
        (root / "crates" / "a" / "src" / "lib.rs").write_text("")
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"], seen["cwd"] = list(argv), kw.get("cwd")
            return subprocess.CompletedProcess(argv, 0, "codec_tests::a_case: test\n", "")

        with mock.patch.object(R.subprocess, "run", fake_run):
            got = R.cargo_tests(root, "crates/a/src/lib.rs")
        self.assertEqual(seen["argv"], ["cargo", "test", "--all-targets", "--", "--list"])
        self.assertEqual(seen["cwd"], root / "crates" / "a")
        # `--list` reports no file, so the pair carries the file that was asked
        # about — there is nothing else it could carry.
        self.assertEqual(got, {("crates/a/src/lib.rs", "codec_tests::a_case")})

    def test_no_cargo_manifest_means_no_subprocess_at_all(self):
        root = self.tmp / "d"
        root.mkdir()
        (root / "a.rs").write_text("")
        with mock.patch.object(R.subprocess, "run") as run:
            self.assertIsNone(R.cargo_tests(root, "a.rs"))
        run.assert_not_called()

    def test_a_missing_cargo_is_not_a_false_claim(self):
        root = self.tmp / "e"
        root.mkdir()
        (root / "Cargo.toml").write_text('[package]\nname = "e"\n')
        (root / "a.rs").write_text("")
        with mock.patch.object(R.subprocess, "run", side_effect=FileNotFoundError("cargo")):
            self.assertIsNone(R.cargo_tests(root, "a.rs"))

    def test_cargo_that_fails_to_compile_is_not_a_false_claim(self):
        # A package that does not build lists nothing. That is "cannot be
        # consulted", not "these tests do not exist".
        root = self.tmp / "f"
        root.mkdir()
        (root / "Cargo.toml").write_text('[package]\nname = "f"\n')
        (root / "a.rs").write_text("")
        with mock.patch.object(R.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 101, "", "error[E0433]")):
            self.assertIsNone(R.cargo_tests(root, "a.rs"))


if __name__ == "__main__":
    unittest.main()
