import json
import unittest

from helpers import TmpCase, claim_text, make_repo, pinned_text, run_cli, write
from claimlock.pins import blob_of_bytes


class Init(TmpCase):
    def test_init_creates_store_once(self):
        d = self.tmp / "p"
        d.mkdir()
        write(d, ".gitignore", "node_modules/")
        rc, out, err = run_cli(d, "init")
        self.assertEqual(rc, 0, err)
        self.assertTrue((d / ".claimlock.toml").is_file())
        self.assertTrue((d / "claims").is_dir())
        self.assertEqual((d / ".gitignore").read_text(), "node_modules/\n.claimlock/\n")
        self.assertIn("claimlock check", out)
        rc, _, err = run_cli(d, "init")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)
        self.assertEqual((d / ".gitignore").read_text().count(".claimlock/"), 1)

    def test_init_into_missing_directory_is_exit_2(self):
        nope = self.tmp / "nope"
        rc, _, err = run_cli(self.tmp, "-C", str(nope), "init")
        self.assertEqual(rc, 2)
        self.assertIn(str(nope), err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(nope.exists())


class New(TmpCase):
    def test_new_scaffolds_a_valid_unverified_claim(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, out, err = run_cli(root, "new", "my-claim", "--area", "api")
        self.assertEqual(rc, 0, err)
        self.assertIn("claims/my-claim.md", out)
        self.assertEqual(run_cli(root, "check")[0], 0)
        self.assertEqual(run_cli(root, "new", "my-claim")[0], 1)
        rc, _, err = run_cli(root, "new", "Bad_Id")
        self.assertEqual(rc, 1)
        self.assertIn("kebab-case", err)


class Check(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=False)
        write(self.root, "a.py", "one\n")
        write(self.root, "b.py", "bee\n")
        write(self.root, "claims/fresh-one.md", pinned_text("fresh-one", [("a.py", blob_of_bytes(b"one\n"))]))
        write(self.root, "claims/stale-one.md", pinned_text("stale-one", [("b.py", blob_of_bytes(b"old\n"))]))
        write(self.root, "claims/unpinned-one.md", pinned_text("unpinned-one", [("a.py", None)]))
        write(self.root, "claims/invalid-one.md", claim_text("invalid-one", status="maybe"))

    def test_human_output_and_census(self):
        rc, out, err = run_cli(self.root, "check")
        self.assertEqual(rc, 1, err)
        self.assertIn("STALE    stale-one", out)
        self.assertIn("b.py: stale", out)
        self.assertIn("UNPINNED unpinned-one", out)
        self.assertIn("INVALID  invalid-one", out)
        self.assertIn("status 'maybe'", out)
        self.assertNotIn("fresh-one", out)
        self.assertIn("claimlock: 4 claims, 3 sources hashed — 1 invalid, 1 unpinned, 1 stale, 0 missing", out)

    def test_json(self):
        rc, out, _ = run_cli(self.root, "check", "--json")
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual((data["claims"], data["sources_hashed"]), (4, 3))
        self.assertEqual(data["counts"], {"invalid": 1, "unpinned": 1, "stale": 1, "missing": 0})
        by_id = {r["id"]: r for r in data["results"]}
        self.assertEqual(by_id["stale-one"]["sources"], [{"path": "b.py", "state": "stale"}])

    def test_area_filter_and_clean_exit(self):
        for cid in ("stale-one", "unpinned-one", "invalid-one"):
            (self.root / "claims" / f"{cid}.md").unlink()
        rc, out, _ = run_cli(self.root, "check")
        self.assertEqual(rc, 0)
        self.assertIn("1 claims, 1 sources hashed — 0 invalid, 0 unpinned, 0 stale, 0 missing", out)
        rc, out, _ = run_cli(self.root, "check", "--area", "elsewhere")
        self.assertEqual(rc, 0)
        self.assertIn("0 claims", out)

    def test_stale_list_search_show(self):
        rc, out, _ = run_cli(self.root, "stale")
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(l.split("\t")[0] for l in out.splitlines()), ["stale-one", "unpinned-one"])

        rc, out, _ = run_cli(self.root, "list", "--status", "verified")
        self.assertEqual(rc, 0)
        self.assertIn("fresh-one", out)
        self.assertIn("stale-one (core) [stale]", out)
        self.assertNotIn("invalid-one", out)

        self.assertEqual(run_cli(self.root, "search", "HOLDS")[0], 0)
        rc, out, _ = run_cli(self.root, "search", "zebra")
        self.assertEqual(rc, 1)
        self.assertIn("nothing matches", out)

        rc, out, err = run_cli(self.root, "show", "stale-one")
        self.assertEqual(rc, 0, err)
        self.assertIn("stale-one (core) — verified", out)
        self.assertIn("[test] s::c", out)
        self.assertIn("b.py — stale", out)
        self.assertEqual(run_cli(self.root, "show", "nope")[0], 1)

    def test_show_works_on_malformed_claims(self):
        rc, out, err = run_cli(self.root, "show", "invalid-one")
        self.assertEqual(rc, 0, err)
        self.assertIn("INVALID:", out)
        self.assertIn("status 'maybe'", out)

        write(self.root, "claims/broken.md", "---\nid: broken\nsources: [x]\n---\nb\n")
        rc, out, err = run_cli(self.root, "show", "broken")
        self.assertEqual(rc, 0, err)
        self.assertIn("broken.md:3", out)
        self.assertNotIn("Traceback", err)


class Unreadable(TmpCase):
    def test_missing_claims_dir_is_exit_2(self):
        root = make_repo(self.tmp / "r", use_git=False)
        (root / "claims").rmdir()
        rc, _, err = run_cli(root, "check")
        self.assertEqual(rc, 2)
        self.assertIn("no claims directory", err)

    def test_bad_config_is_exit_2(self):
        root = make_repo(self.tmp / "r", use_git=False, config="bogus = 1\n")
        rc, _, err = run_cli(root, "check")
        self.assertEqual(rc, 2)
        self.assertIn("unknown key", err)

    def test_empty_store_reports_zero(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 0)
        self.assertIn("0 claims, 0 sources hashed", out)

    def test_dir_option(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, out, _ = run_cli(self.tmp, "-C", str(root), "check")
        self.assertEqual(rc, 0)
        self.assertIn("0 claims", out)


if __name__ == "__main__":
    unittest.main()
