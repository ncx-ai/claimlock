### Task 6: The scoped team gate, owed listing, `who`, verifier lines, owner filters

Spec §5.1, §5.4, §5.5 and amendment T3. `check --changed <base>` blocks only on claims a change touched; everything else that is wrong is listed as pre-existing; `owed` claims are listed and never fail. Who verified a pin comes from git history.

**Files:**
- Modify: `lib/claimlock/gitio.py` (add `merge_base`, `changed_since`, `verifier`, `commits_behind`), `lib/claimlock/cli.py` (`cmd_check`, `cmd_show`, `cmd_stale`, `cmd_list`, add `cmd_who`, `CI_SNIPPET`, parser), `README.md` (commands table: `claimlock who`, `check --changed`)
- Create: `tests/test_team_gate.py`

**Interfaces:**
- Consumes: `claims.NON_FRESH/Result/Claim.owed_by/owed_since`, `ops.NeedsIdentity` (mapped to exit 2), `gitio.user_email/in_git` (Tasks 2–5).
- Produces: `gitio.merge_base(root, base)`, `gitio.changed_since(root, commit) -> list[str]`, `gitio.verifier(root, claim_rel, blob) -> (email, iso, sha) | None`, `gitio.commits_behind(root, commit) -> int | None`; CLI `check --changed BASE`, `who ID`, `stale/list --owed-by EMAIL | --mine`.

- [ ] **Step 1: Write the failing tests**

`tests/test_team_gate.py`:
```python
import json
import os
import shutil
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")
NO_IDENTITY = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


@NEED_GIT
class ScopedGate(TmpCase):
    def setUp(self):
        super().setUp()
        r = self.root = make_repo(self.tmp / "r", use_git=True)
        write(r, ".gitignore", ".claimlock/\n")
        write(r, "src1.py", "one\n")
        write(r, "src2.py", "two\n")
        write(r, "claims/c1.md", claim_text("c1", sources=("src1.py",)))
        write(r, "claims/c2.md", claim_text("c2", sources=("src2.py",)))
        run_cli(r, "verify", "c1", "c2")
        git(r, "add", "-A")
        git(r, "commit", "-qm", "base")
        write(r, "src2.py", "TWO\n")           # drift already on main, never re-verified
        git(r, "commit", "-qam", "drift on main")
        git(r, "checkout", "-q", "-b", "feature")

    def gate(self, *extra):
        return run_cli(self.root, "check", "--changed", "main", *extra)

    def test_a_change_to_a_cited_source_blocks_and_old_drift_is_listed(self):
        write(self.root, "src1.py", "ONE\n")
        git(self.root, "commit", "-qam", "change src1")
        rc, out, err = self.gate()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("STALE    c1", out)
        self.assertIn("pre-existing (not changed here):", out)
        self.assertIn("  c2: stale", out)
        self.assertIn("claimlock: 2 claims (1 in scope)", out)

    def test_an_unrelated_change_does_not_block_on_old_drift(self):
        write(self.root, "other.txt", "x\n")
        git(self.root, "add", "other.txt")
        git(self.root, "commit", "-qm", "unrelated")
        rc, out, _ = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertIn("  c2: stale", out)
        self.assertIn("(0 in scope)", out)
        self.assertEqual(run_cli(self.root, "check")[0], 1, "plain check stays strict")

    def test_an_owed_claim_does_not_block(self):
        write(self.root, "src1.py", "ONE\n")
        git(self.root, "commit", "-qam", "change src1")
        self.assertEqual(run_cli(self.root, "owe", "c1", "--to", "bob@example.com")[0], 0)
        git(self.root, "commit", "-qam", "hand off c1")
        rc, out, _ = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"OWED     c1 → bob@example\.com since [0-9a-f]{7}, 1 commit ago")

    def test_a_claim_committed_with_an_uncommitted_pin_blocks(self):
        write(self.root, "src1.py", "ONE\n")
        run_cli(self.root, "verify", "c1")
        git(self.root, "add", "claims/c1.md")   # the source edit stays uncommitted and unstaged
        git(self.root, "commit", "-qm", "claim only")
        rc, out, _ = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("UNANCHORED c1", out)

    def test_json_marks_scope_and_blocking(self):
        write(self.root, "src1.py", "ONE\n")
        git(self.root, "commit", "-qam", "change src1")
        rc, out, _ = self.gate("--json")
        data = json.loads(out)
        by_id = {r["id"]: r for r in data["results"]}
        self.assertEqual(data["scope"], ["c1"])
        self.assertEqual((by_id["c1"]["in_scope"], by_id["c1"]["blocking"]), (True, True))
        self.assertEqual((by_id["c2"]["in_scope"], by_id["c2"]["blocking"]), (False, False))

    def test_bad_base_is_exit_2(self):
        rc, _, err = run_cli(self.root, "check", "--changed", "no-such-ref")
        self.assertEqual(rc, 2)
        self.assertIn("merge base", err)


class GateOutsideGit(TmpCase):
    def test_changed_needs_git(self):
        root = make_repo(self.tmp / "r", use_git=False)
        rc, _, err = run_cli(root, "check", "--changed", "main")
        self.assertEqual(rc, 2)
        self.assertIn("git repository", err)


@NEED_GIT
class WhoVerified(TmpCase):
    def test_verifier_survives_later_commits_by_someone_else(self):
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        run_cli(root, "verify", "c")
        rc, out, _ = run_cli(root, "who", "c")
        self.assertEqual(out, "a.py\tuncommitted\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c")
        git(root, "-c", "user.email=other@example.com", "commit", "-q", "--allow-empty", "-m", "unrelated")
        rc, out, _ = run_cli(root, "who", "c")
        self.assertRegex(out, r"^a\.py\tt@example\.com\t\d{4}-\d\d-\d\dT[^\t]+\t[0-9a-f]{7}\n$")
        rc, out, _ = run_cli(root, "show", "c")
        self.assertIn("verified by t@example.com at", out)


class WhoOutsideGit(TmpCase):
    def test_unknown_without_git_and_unpinned(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "who", "c")[1], "a.py\tunpinned\n")
        run_cli(root, "verify", "c")
        self.assertEqual(run_cli(root, "who", "c")[1], "a.py\tunknown\n")
        self.assertEqual(run_cli(root, "who", "nope")[0], 1)


class OwnerFilters(TmpCase):
    def test_owed_by_and_mine(self):
        root = make_repo(self.tmp / "r", use_git=False)
        write(root, "a.py", "one\n")
        for cid in ("c1", "c2"):
            write(root, f"claims/{cid}.md", claim_text(cid, sources=("a.py",)))
        run_cli(root, "verify", "c1", "c2")
        write(root, "a.py", "two\n")
        run_cli(root, "owe", "c1", "--to", "bob@example.com")
        run_cli(root, "owe", "c2", "--to", "amy@example.com")
        rc, out, _ = run_cli(root, "stale", "--owed-by", "bob@example.com")
        self.assertEqual((rc, out), (0, "c1\tcore\towed\tbob@example.com\n"))
        rc, out, _ = run_cli(root, "list", "--owed-by", "amy@example.com")
        self.assertIn("c2 (core) [owed → amy@example.com]", out)
        self.assertNotIn("c1", out)
        rc, _, err = run_cli(root, "list", "--mine", env=NO_IDENTITY)
        self.assertEqual(rc, 2)
        self.assertIn("user.email", err)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -p "test_team_gate.py" -v`
Expected: `unrecognized arguments: --changed` / `invalid choice: 'who'` / `unrecognized arguments: --owed-by` (rc 2 where 0/1 expected).

- [ ] **Step 3: Implement**

`lib/claimlock/gitio.py`:
```python
def merge_base(root, base):
    return _text(run(root, "merge-base", base, "HEAD")) or None


def changed_since(root, commit):
    """Paths changed by commits in commit..HEAD, relative to (and limited to) `root`."""
    out = _text(run(root, "diff", "--name-only", "--relative", "--no-renames", commit, "HEAD"))
    return _lines(out) if out is not None else []


def verifier(root, claim_rel, blob):
    """(author email, ISO time, short sha) of the latest commit that added or
    removed `blob: <sha>` in the claim file, or None if no commit did."""
    out = _text(run(root, "log", "-1", "--format=%ae%x09%aI%x09%h", "-S", f"blob: {blob}", "--", claim_rel))
    if not out:
        return None
    parts = out.split("\t")
    return tuple(parts) if len(parts) == 3 else None


def commits_behind(root, commit):
    out = _text(run(root, "rev-list", "--count", f"{commit}..HEAD"))
    return int(out) if out and out.isdigit() else None
```

`lib/claimlock/cli.py`:

Replace `CI_SNIPPET` with:
```python
CI_SNIPPET = """\
Gate a change in CI on the claims it touched (pre-existing drift is listed, not blocking):

    claimlock self-test && claimlock check --changed origin/main && claimlock refs

Check the whole store locally:

    claimlock check

Then: claimlock new <id> --area <area>   (write the claim, cite evidence and sources)
      claimlock verify <id>              (pins every source; only after checking it)
      claimlock owe <id> --to <email>    (hand the re-check to someone, instead)
"""
```

Replace `cmd_check` with:
```python
def _failing(r):
    return bool(r.problems) or r.state in C.NON_FRESH


def _print_failing(r):
    if r.problems:
        print(f"{_paint('31', 'INVALID ')} {r.claim.id}")
        for x in r.problems:
            print(f"         {x}")
    if r.state in C.NON_FRESH:
        print(f"{_paint('33', r.state.upper().ljust(8))} {r.claim.id}")
        for path, st in r.per_source:
            if st != "fresh":
                print(f"         {path}: {st}")
        print(f"         {HINT[r.state].format(id=r.claim.id)}")


def _owed_line(project, r):
    since = r.claim.owed_since or "?"
    behind = gitio.commits_behind(project.root, since) if since not in ("?", "none") else None
    ago = "" if behind is None else f", {behind} commit{'' if behind == 1 else 's'} ago"
    return f"OWED     {r.claim.id} → {r.claim.owed_by} since {since}{ago}"


def cmd_check(args):
    project, hasher, results = _evaluate(args)
    scope = None
    if args.changed:
        if not gitio.in_git(project.root):
            print("claimlock: --changed needs a git repository", file=sys.stderr)
            return 2
        base = gitio.merge_base(project.root, args.changed)
        if base is None:
            print(f"claimlock: cannot find a merge base between {args.changed!r} and HEAD", file=sys.stderr)
            return 2
        changed = set(gitio.changed_since(project.root, base))
        scope = {r.claim.id for r in results
                 if _rel(project, r.claim.path) in changed or any(s.path in changed for s in r.claim.sources)}

    def in_scope(r):
        return scope is None or r.claim.id in scope

    blocking = [r for r in results if _failing(r) and in_scope(r)]
    elsewhere = [r for r in results if _failing(r) and not in_scope(r)]
    owed = [r for r in results if r.claim.status == "owed" and not r.problems]
    blocking_ids = {r.claim.id for r in blocking}
    counts = {"invalid": sum(1 for r in blocking if r.problems)}
    counts.update({s: sum(1 for r in blocking if r.state == s) for s in C.NON_FRESH})
    if args.json:
        print(json.dumps({
            "claims": len(results),
            "sources_hashed": hasher.hashed,
            "counts": counts,
            "scope": sorted(scope) if scope is not None else None,
            "results": [{
                "id": r.claim.id, "area": r.claim.area, "status": r.claim.status,
                "problems": r.problems, "state": r.state,
                "sources": [{"path": p, "state": s} for p, s in r.per_source],
                "in_scope": in_scope(r), "blocking": r.claim.id in blocking_ids,
                "owed_by": r.claim.owed_by,
            } for r in results],
        }, indent=2))
    else:
        for r in blocking:
            _print_failing(r)
        if elsewhere:
            print("pre-existing (not changed here):")
            for r in elsewhere:
                print(f"  {r.claim.id}: {'invalid' if r.problems else r.state}")
        for r in owed:
            print(_owed_line(project, r))
        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        if owed:
            summary += f", {len(owed)} owed"
        head = f"{len(results)} claims" + (f" ({len(scope)} in scope)" if scope is not None else "")
        print(f"claimlock: {head}, {hasher.hashed} sources hashed — {summary}")
    return 1 if blocking else 0
```

Add the verifier helper and `who`, and extend `cmd_show`:
```python
def _verified(project, in_git, claim_rel, source):
    """("unpinned",) | ("unknown",) | ("uncommitted",) | ("verified", email, iso, sha)."""
    if not source.blob:
        return ("unpinned",)
    if not in_git:
        return ("unknown",)
    v = gitio.verifier(project.root, claim_rel, source.blob)
    return ("verified", *v) if v else ("uncommitted",)


def cmd_who(args):
    project = _project(args)
    c = _find(project, args.id)
    if c is None:
        print(f"claimlock: no claim {args.id!r}", file=sys.stderr)
        return 1
    in_git = gitio.in_git(project.root)
    for s in c.sources:
        v = _verified(project, in_git, _rel(project, c.path), s)
        print("\t".join([s.path, *(v[1:] if v[0] == "verified" else v)]))
    return 0
```
In `cmd_show`, compute `in_git = gitio.in_git(project.root)` once, and change the per-source print to:
```python
            v = _verified(project, in_git, _rel(project, c.path), s)
            note = {"unpinned": "", "unknown": " — verified by unknown (no git)",
                    "uncommitted": " — uncommitted (verifier known once committed)"}.get(v[0])
            if note is None:
                note = f" — verified by {v[1]} at {v[2]} ({v[3]})"
            print(f"  {s.path} — {states.get(s.path, '-')} ({pin}){note}")
```

Owner filters:
```python
def _owner(args, project):
    if getattr(args, "mine", False):
        email = gitio.user_email(project.root)
        if not email:
            raise ops.NeedsIdentity("--mine needs git config user.email (or use --owed-by <email>)")
        return email
    return getattr(args, "owed_by", None)
```
`cmd_stale`:
```python
def cmd_stale(args):
    project, _, results = _evaluate(args)
    owner = _owner(args, project)
    rc = 0
    for r in results:
        if r.claim.status == "owed" and (owner is None or r.claim.owed_by == owner):
            print(f"{r.claim.id}\t{r.claim.area}\towed\t{r.claim.owed_by}")
        elif owner is None and r.state in C.NON_FRESH:
            rc = 1
            paths = ",".join(p for p, s in r.per_source if s != "fresh")
            print(f"{r.claim.id}\t{r.claim.area}\t{r.state}\t{paths}")
    return rc
```
`cmd_list`: at the top `project, _, results = _evaluate(args)` and `owner = _owner(args, project)`; inside the loop add `if owner is not None and not (r.claim.status == "owed" and r.claim.owed_by == owner): continue`.

Parser: on `check` add `p.add_argument("--changed", metavar="BASE", help="block only on claims whose sources or files changed since the merge base with BASE")`; on `stale` and `list` add a mutually exclusive group with `--owed-by EMAIL` and `--mine`; register after `diff`:
```python
    p = add("who", cmd_who, "who verified each of a claim's pins, from git history")
    p.add_argument("id")
```

`README.md` — add a `claimlock who` row, and extend the `claimlock check` row to mention `--changed <base>`.

- [ ] **Step 4: Run to verify it passes**

Run the focused file, then the full suite plain and with `-W error::ResourceWarning`. Existing plain-`check` output assertions must pass unchanged (the plain path prints the same lines as before, plus `OWED` lines only when owed claims exist). Report the observed total (+10 tests).

- [ ] **Step 5: Watch scoping fail for its own reason**

(a) Temporarily make `in_scope` return True always; `test_an_unrelated_change_does_not_block_on_old_drift` fails. Re-apply.
(b) Temporarily drop the `_rel(project, r.claim.path) in changed or` clause; `test_a_claim_committed_with_an_uncommitted_pin_blocks` fails. Re-apply. Suite green.

- [ ] **Step 6: Commit**

```bash
git add lib/claimlock/gitio.py lib/claimlock/cli.py README.md tests/test_team_gate.py
git commit -m "feat: check --changed gates only what a change touched; who verified a pin" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
