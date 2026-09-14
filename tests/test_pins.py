import json
import os
import shutil
import subprocess
import time
import unittest

from helpers import TmpCase, write
from claimlock import pins
from claimlock.pins import Hasher, blob_of_bytes

OLD = time.time_ns() - 100 * 1_000_000_000  # 100 s ago: old enough to cache


class Blob(TmpCase):
    def test_known_git_values(self):
        self.assertEqual(blob_of_bytes(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
        self.assertEqual(blob_of_bytes(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_matches_git_hash_object_no_filters(self):
        p = write(self.tmp, "crlf.txt", "")
        p.write_bytes(b"line one\r\nline two\r\n\x00binary")
        out = subprocess.run(["git", "hash-object", "--no-filters", str(p)],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(blob_of_bytes(p.read_bytes()), out)


class HasherCase(TmpCase):
    def cache(self):
        return self.tmp / ".claimlock" / "cache" / "stat.json"

    def test_missing_and_directory_are_none(self):
        (self.tmp / "d").mkdir()
        h = Hasher(self.tmp, None)
        self.assertIsNone(h.blob("nope.txt"))
        self.assertIsNone(h.blob("d"))

    def test_hashes_and_counts(self):
        write(self.tmp, "a.txt", "hello\n")
        h = Hasher(self.tmp, None)
        self.assertEqual(h.blob("a.txt"), "ce013625030ba8dba906f756967f9e9ca394464a")
        self.assertEqual(h.hashed, 1)

    def test_old_entry_is_cached_and_reused_when_stat_matches(self):
        p = write(self.tmp, "a.txt", "hello\n")
        os.utime(p, ns=(OLD, OLD))
        h = Hasher(self.tmp, self.cache())
        first = h.blob("a.txt")
        h.save()
        self.assertTrue(self.cache().is_file())
        # Same size, same mtime, different bytes: the cache is trusted (documented trade-off).
        p.write_text("HELLO\n")
        os.utime(p, ns=(OLD, OLD))
        self.assertEqual(Hasher(self.tmp, self.cache()).blob("a.txt"), first)
        # Any mtime change invalidates the entry.
        os.utime(p, ns=(OLD + 1, OLD + 1))
        self.assertEqual(Hasher(self.tmp, self.cache()).blob("a.txt"), blob_of_bytes(b"HELLO\n"))

    def test_recent_mtime_is_never_cached(self):
        write(self.tmp, "a.txt", "hello\n")
        h = Hasher(self.tmp, self.cache())
        h.blob("a.txt")
        h.save()
        data = json.loads(self.cache().read_text()) if self.cache().exists() else {}
        self.assertNotIn("a.txt", data)

    def test_corrupt_cache_is_ignored(self):
        write(self.tmp, "a.txt", "hello\n")
        self.cache().parent.mkdir(parents=True)
        self.cache().write_text("{not json")
        self.assertEqual(Hasher(self.tmp, self.cache()).blob("a.txt"), blob_of_bytes(b"hello\n"))


if __name__ == "__main__":
    unittest.main()
