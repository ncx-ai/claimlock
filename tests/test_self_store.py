"""claimlock keeps claims about claimlock (design §3.3).

`claims/` is not decoration: this test runs `check` and `evidence` against
this repository's OWN store, so a self-claim going stale — a cited source
changing shape, an evidence ref naming a test that got renamed or deleted —
fails the suite the same way any other regression would. The claims
themselves are not what makes this a gate; a claim store nothing checks is.

This repository's own `docs/plans/` holds dated, historical planning
documents that embed literal `Claim: `<id>`` strings as worked examples of
the marker syntax and as verbatim snippets of test code written for earlier
tasks (e.g. `docs/plans/2026-09-14-claimlock/
07-refs-affected-import-selftest.md`) — every one of those sits inside a ```
fence. That used to make bare `claimlock refs` exit 1 on this repository's
own store (dangling markers that were invisible only because `claims/` did
not exist yet, i.e. every command that reads the store used to exit 2
first); `refs.scan` now skips a marker inside a fenced code block (it
documents the syntax, it does not cite a claim), which closes that backlog
without touching `docs/plans/` at all, so `test_refs_exits_0_on_this_repos_own_store`
below can assert the plain exit code.

Citation completeness (`refs --orphans`) is still checked separately from
that, on the census's uncited-ids list rather than the return code: an
uncited claim never fails `refs`'s exit code by design (a documentation gap,
not a false statement), so this asserts what this task is responsible for —
every self-claim is cited — by checking that none of the census's uncited
ids are self-claims.
"""
import subprocess
import unittest

from helpers import REPO, run_cli

SELF_CLAIM_IDS = {
    "hooks-always-exit-0",
    "post-edit-spawns-no-git",
    "search-exits-1-only-on-no-match",
    "check-exits-2-when-store-unreadable",
}


def _git_checkout_available():
    """True when `REPO` is a git checkout `git` can inspect — mirrors the
    skip pattern in test_plugin_manifest.py's
    test_tracked_files_have_no_machine_specific_paths, so this gate degrades
    silently (not failing) when run from a `git archive` copy with no `.git`
    directory, a case the suite is sometimes run from by a reviewer."""
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO,
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


class SelfStore(unittest.TestCase):
    def test_check_passes_on_this_repos_own_claims(self):
        rc, out, err = run_cli(REPO, "check")
        self.assertEqual(rc, 0, out + err)

    def test_evidence_resolves_on_this_repos_own_claims(self):
        rc, out, err = run_cli(REPO, "evidence")
        self.assertEqual(rc, 0, out + err)

    def test_refs_exits_0_on_this_repos_own_store(self):
        # Was exit 1 (dangling markers) until refs.scan started skipping
        # markers quoted inside fenced code blocks — see the module docstring.
        rc, out, err = run_cli(REPO, "refs")
        self.assertEqual(rc, 0, out + err)

    def test_self_claims_are_all_cited(self):
        if not _git_checkout_available():
            self.skipTest("not a git checkout")
        rc, out, err = run_cli(REPO, "refs", "--orphans", "--full")
        self.assertNotIn("Traceback", out + err)
        uncited = {line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("UNCITED ")}
        self.assertFalse(uncited & SELF_CLAIM_IDS, uncited & SELF_CLAIM_IDS)


if __name__ == "__main__":
    unittest.main()
