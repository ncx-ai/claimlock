"""Pins inside git are the blob git stores, so clones with different line-ending
settings agree; outside git a pin is the file's raw bytes."""
import os
import re
import shutil
import subprocess
import time
import unittest
from unittest import mock

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write
from claimlock import gitio, pins
from claimlock.pins import Hasher, blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")
OLD = time.time_ns() - 100 * 1_000_000_000


def clone(bare, dest, *config):
    subprocess.run(["git", *config, "clone", "-q", str(bare), str(dest)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(dest, "config", k, v)


@NEED_GIT
class LineEndingsAcrossClones(TmpCase):
    def test_lf_and_crlf_clones_agree_on_every_pin(self):
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True, capture_output=True)
        a = self.tmp / "a"
        clone(bare, a)
        git(a, "config", "core.autocrlf", "false")
        run_cli(a, "init")
        # The claim STORE format (unlike a hashed source) is parsed as literal
        # text and rejects CR outright (tests/test_claims.py), so it needs its
        # own LF-normalizing attribute — the same thing README already advises
        # for a team that spans line-ending settings — independent of whether
        # a cited source hashes the same on both clones, which is what this
        # test actually exercises.
        write(a, ".gitattributes", "claims/*.md text eol=lf\n")
        write(a, "src/limit.py", "MAX = 5\n\ndef clamp(n):\n    return min(n, MAX)\n")
        write(a, "claims/c.md", claim_text("c", sources=("src/limit.py",)))
        git(a, "add", "-A")
        self.assertEqual(run_cli(a, "verify", "c")[0], 0)
        git(a, "add", "-A")
        git(a, "commit", "-qm", "verified on LF")
        git(a, "push", "-q", "origin", "main")

        b = self.tmp / "b"
        clone(bare, b, "-c", "core.autocrlf=true")
        git(b, "config", "core.autocrlf", "true")
        (b / "src" / "limit.py").unlink()
        git(b, "checkout", "--", "src/limit.py")
        self.assertIn(b"\r\n", (b / "src" / "limit.py").read_bytes(), "precondition: B holds CRLF bytes")

        rc, out, _ = run_cli(b, "check")
        self.assertEqual(rc, 0, out)
        pins_of = lambda: re.findall(r"blob: ([0-9a-f]{40})", (b / "claims" / "c.md").read_text())
        before = pins_of()
        self.assertEqual(len(before), 1)
        self.assertEqual(run_cli(b, "verify", "c")[0], 0)
        # Compare pins only: until Task 4, verify still rewrites verified_at.
        self.assertEqual(pins_of(), before,
                         "re-verifying on the CRLF clone must not change the shared pin")


@NEED_GIT
class SubdirectoryHashing(TmpCase):
    """`--stdin-paths` resolves a relative path from the repository TOP LEVEL,
    not the invoking process's cwd (see gitio.hash_paths' docstring) — so
    when claimlock's root is a subdirectory of a larger git work tree, a
    bare root-relative path would name the wrong (usually nonexistent)
    file. This is what forces hash_paths to pass absolute paths; the
    LineEndingsAcrossClones test above cannot exercise it because its
    project root IS the repository top level, so relative and absolute
    paths happen to coincide there."""

    def test_hash_paths_finds_a_file_when_root_is_a_repo_subdirectory(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(repo, "config", k, v)
        proj = repo / "sub"
        write(proj, "a.txt", "hello\n")
        got = gitio.hash_paths(proj, ["a.txt"])
        self.assertEqual(got, {"a.txt": blob_of_bytes(b"hello\n")})


class RawOutsideGit(TmpCase):
    def test_a_line_ending_change_is_stale_outside_git(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.txt", "one\ntwo\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.txt",)))
        run_cli(root, "verify", "c")
        (root / "a.txt").write_bytes(b"one\r\ntwo\r\n")
        rc, out, _ = run_cli(root, "check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE", out)


class HasherModes(TmpCase):
    def test_a_raw_cache_entry_is_never_trusted_in_git_mode(self):
        p = write(self.tmp, "a.txt", "hello\n")
        os.utime(p, ns=(OLD, OLD))
        cache = self.tmp / "stat.json"
        raw = Hasher(self.tmp, cache, mode="raw")
        raw.blob("a.txt")
        raw.save()
        with mock.patch("claimlock.gitio.hash_paths", return_value={"a.txt": "f" * 40}) as hp:
            got = Hasher(self.tmp, cache, mode="git").blob("a.txt")
        self.assertEqual(got, "f" * 40)
        self.assertEqual(hp.call_count, 1)

    def test_prime_hashes_many_files_in_one_git_call(self):
        for n in "abc":
            write(self.tmp, f"{n}.txt", n)
        fake = {f"{n}.txt": n * 40 for n in "abc"}
        with mock.patch("claimlock.gitio.hash_paths", return_value=fake) as hp:
            h = Hasher(self.tmp, None, mode="git")
            h.prime(["a.txt", "b.txt", "c.txt", "missing.txt"])
            got = [h.blob(f"{n}.txt") for n in "abc"]
        self.assertEqual(got, ["a" * 40, "b" * 40, "c" * 40])
        self.assertEqual(hp.call_count, 1)
        self.assertEqual(hp.call_args[0][1], ["a.txt", "b.txt", "c.txt"])

    def test_a_primed_hash_is_discarded_when_the_file_changed_after_priming(self):
        write(self.tmp, "a.txt", "one")
        h = Hasher(self.tmp, None, mode="git")
        with mock.patch("claimlock.gitio.hash_paths", return_value={"a.txt": "1" * 40}):
            h.prime(["a.txt"])
        write(self.tmp, "a.txt", "one, and longer")
        with mock.patch("claimlock.gitio.hash_paths", return_value={"a.txt": "2" * 40}):
            self.assertEqual(h.blob("a.txt"), "2" * 40)

    def test_git_failure_falls_back_to_raw_bytes(self):
        write(self.tmp, "a.txt", "hello\n")
        with mock.patch("claimlock.gitio.hash_paths", return_value=None):
            self.assertEqual(Hasher(self.tmp, None, mode="git").blob("a.txt"), blob_of_bytes(b"hello\n"))

    def test_use_cache_false_ignores_a_matching_cache_entry(self):
        p = write(self.tmp, "a.txt", "hello\n")
        os.utime(p, ns=(OLD, OLD))
        st = p.stat()
        h = Hasher(self.tmp, None, mode="raw")
        h.cache["a.txt"] = [st.st_size, st.st_mtime_ns, "0" * 40, "raw"]
        self.assertEqual(h.blob("a.txt"), "0" * 40)
        self.assertEqual(h.blob("a.txt", use_cache=False), blob_of_bytes(b"hello\n"))


if __name__ == "__main__":
    unittest.main()
