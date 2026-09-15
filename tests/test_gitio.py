import shutil
import unittest
import unittest.mock as mock
from pathlib import Path

from helpers import TmpCase, git, make_repo, write
from claimlock import claims as C
from claimlock import gitio
from claimlock import project as P
from claimlock.pins import blob_of_bytes

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


class NoGit(TmpCase):
    def test_everything_degrades_to_empty(self):
        self.assertFalse(gitio.in_git(self.tmp))
        self.assertIsNone(gitio.head(self.tmp))
        self.assertIsNone(gitio.cat_blob(self.tmp, "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"))
        self.assertEqual(gitio.changed_paths(self.tmp, None, "HEAD"), [])
        self.assertEqual(gitio.head_mark_paths(self.tmp), [])

    def test_root_is_ignored_is_false_outside_git(self):
        self.assertFalse(gitio.root_is_ignored(self.tmp))


@NEED_GIT
class WithGit(TmpCase):
    def setUp(self):
        super().setUp()
        self.top = self.tmp / "top"
        self.top.mkdir()
        git(self.top, "init", "-q", "-b", "main")
        git(self.top, "config", "user.email", "t@example.com")
        git(self.top, "config", "user.name", "t")
        git(self.top, "config", "commit.gpgsign", "false")

    def commit(self, msg):
        git(self.top, "add", "-A")
        git(self.top, "commit", "-q", "-m", msg)
        return git(self.top, "rev-parse", "HEAD").strip()

    def test_head_blob_and_changed_paths_relative_to_a_subdirectory(self):
        self.assertTrue(gitio.in_git(self.top))
        self.assertIsNone(gitio.head(self.top))  # unborn branch
        write(self.top, "proj/a.py", "one\n")
        write(self.top, "other/z.py", "zed\n")
        first = self.commit("one")
        proj = self.top / "proj"
        self.assertEqual(gitio.head(proj), first)
        blob = blob_of_bytes(b"one\n")
        self.assertEqual(gitio.cat_blob(proj, blob), b"one\n")
        # the root commit: every file under the project dir, relative to it
        self.assertEqual(gitio.changed_paths(proj, None, first), ["a.py"])
        write(self.top, "proj/b.py", "bee\n")
        write(self.top, "other/z.py", "zed2\n")
        second = self.commit("two")
        self.assertEqual(gitio.changed_paths(proj, first, second), ["b.py"])
        # an unreachable "old" falls back to the new commit's own changes
        self.assertEqual(gitio.changed_paths(proj, "0" * 40, second), ["b.py"])

    def test_head_mark_paths_exist_after_a_commit(self):
        write(self.top, "a.py", "one\n")
        self.commit("one")
        marks = gitio.head_mark_paths(self.top)
        self.assertTrue(any(m.endswith("logs/HEAD") for m in marks), marks)
        self.assertTrue(any(m.endswith("refs/heads/main") for m in marks), marks)
        self.assertTrue(all(Path(m).is_absolute() for m in marks))

    def test_root_is_ignored_only_inside_an_ignored_directory(self):
        write(self.top, ".gitignore", "scratch/\n")
        (self.top / "scratch" / "proj").mkdir(parents=True)
        (self.top / "open").mkdir()
        self.assertTrue(gitio.root_is_ignored(self.top / "scratch" / "proj"))
        self.assertFalse(gitio.root_is_ignored(self.top / "open"))
        self.assertFalse(gitio.root_is_ignored(self.top))


@NEED_GIT
class AnchoringOrder(TmpCase):
    """`anchored_blobs` reads the index first and walks history only when a
    pin being checked is not staged: the same answer, no `rev-list` in the
    common case (every pin is HEAD or staged content)."""

    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "r", use_git=True)
        write(self.root, "a.py", "one\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "one")

    def anchors(self, blob):
        calls, real = [], gitio.run

        def recording(root, *args):
            calls.append(args)
            return real(root, *args)

        with mock.patch.object(gitio, "run", recording):
            got = C.anchors_for(P.load(self.root), [C.Source("a.py", blob)])
        return got, [a for a in calls if "rev-list" in a]

    def test_no_history_walk_when_every_pin_is_staged(self):
        blob = blob_of_bytes(b"one\n")
        got, rev_lists = self.anchors(blob)
        self.assertTrue(got.ok("a.py", blob))
        self.assertEqual(rev_lists, [])

    def test_a_pin_only_in_history_still_walks_it_and_anchors(self):
        write(self.root, "a.py", "two\n")
        git(self.root, "commit", "-qam", "two")
        blob = blob_of_bytes(b"one\n")
        got, rev_lists = self.anchors(blob)
        self.assertTrue(got.ok("a.py", blob))
        self.assertEqual(len(rev_lists), 1)

    def test_a_pin_nowhere_is_not_anchored(self):
        blob = blob_of_bytes(b"never committed\n")
        got, rev_lists = self.anchors(blob)
        self.assertFalse(got.ok("a.py", blob))
        self.assertEqual(len(rev_lists), 1)


@NEED_GIT
class DirtyPaths(TmpCase):
    def test_staged_mv_lists_both_old_and_new_paths(self):
        root = self.tmp / "r"
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "t")
        git(root, "config", "commit.gpgsign", "false")
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        git(root, "mv", "a.py", "b.py")
        self.assertEqual(sorted(gitio.dirty_paths(root)), ["a.py", "b.py"])


if __name__ == "__main__":
    unittest.main()
