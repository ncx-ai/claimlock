### Task 5: `claimlock owe` and `claimlock resolve`

Spec §5.2–§5.3, amendments T4–T5. `owe` hands off a re-check in the claim file. `resolve` settles conflicted `sources` pins after a merge: a pin is kept only when it equals the merged working-tree content of its source, so a kept pin is exactly what one side verified; otherwise the claim becomes `owed` by whoever is merging. Conflicts outside the sources block are left for a person.

**Files:**
- Modify: `lib/claimlock/gitio.py` (add `user_email`, `short_head`), `lib/claimlock/ops.py` (add `NeedsIdentity`, `owe`), `lib/claimlock/cli.py` (`cmd_owe`, `cmd_resolve`, register both, map `NeedsIdentity` to exit 2), `README.md` (command table rows for `owe` and `resolve`)
- Create: `lib/claimlock/merge.py`, `tests/test_owe.py`, `tests/test_resolve.py`

**Interfaces:**
- Consumes: `claims.load_claims/freshness/anchors_for/open_hasher/EMAIL_RE`, `Claim.conflicted/owed_by`, `frontmatter.split/parse/rewrite(set_fields, remove)`, `ops._write`, `Hasher.blob(use_cache=False)`, `project.safe_source` (Tasks 2–4).
- Produces: `gitio.user_email(root) -> str | None`, `gitio.short_head(root) -> str | None`; `ops.NeedsIdentity`; `ops.owe(project, cid, to=None, reason=None, today=None) -> (owed_by, owed_since)`; `merge.hunks(lines)`, `merge.resolve_claim(project, claim) -> (outcome, message)`. Task 6 lists owed claims; Task 7 reports them.

- [ ] **Step 1: Write the failing tests**

`tests/test_owe.py`:
```python
import os
import shutil
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")
NO_IDENTITY = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


class Owe(TmpCase):
    def store(self, use_git=False):
        root = make_repo(self.tmp / "r", use_git=use_git)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        if use_git:
            git(root, "add", "-A")
            git(root, "commit", "-qm", "c")
        return root

    def test_owe_a_stale_claim_to_someone(self):
        root = self.store()
        write(root, "a.py", "two\n")
        rc, out, err = run_cli(root, "owe", "c", "--to", "bob@example.com", "--reason", "MAX moved into config",
                               env=NO_IDENTITY)
        self.assertEqual(rc, 0, err)
        self.assertIn("owed c → bob@example.com (since none)", out)
        text = (root / "claims" / "c.md").read_text()
        self.assertIn("status: owed", text)
        self.assertIn("owed_since: none", text)
        self.assertRegex(text, r"\nOwed \d{4}-\d\d-\d\d by unknown: MAX moved into config\n$")
        self.assertEqual(run_cli(root, "check")[0], 0, "owed never fails check")

    def test_refusals(self):
        root = self.store()
        rc, _, err = run_cli(root, "owe", "c", "--to", "bob@example.com")
        self.assertEqual(rc, 1)
        self.assertIn("is fresh", err)
        write(root, "claims/u.md", claim_text("u", sources=("a.py",)))
        rc, _, err = run_cli(root, "owe", "u", "--to", "bob@example.com")
        self.assertEqual(rc, 1)
        self.assertIn("unverified", err)
        write(root, "a.py", "two\n")
        self.assertEqual(run_cli(root, "owe", "c", "--to", "bob@example.com")[0], 0)
        rc, _, err = run_cli(root, "owe", "c", "--to", "bob@example.com")
        self.assertEqual(rc, 1)
        self.assertIn("already owed", err)
        self.assertEqual(run_cli(root, "owe", "c", "--to", "amy@example.com")[0], 0)
        self.assertEqual(run_cli(root, "owe", "nope", "--to", "bob@example.com")[0], 1)

    def test_no_identity_outside_git_is_exit_2(self):
        root = self.store()
        write(root, "a.py", "two\n")
        rc, _, err = run_cli(root, "owe", "c", env=NO_IDENTITY)
        self.assertEqual(rc, 2)
        self.assertIn("--to", err)

    @NEED_GIT
    def test_inside_git_defaults_to_your_email_and_head(self):
        root = self.store(use_git=True)
        write(root, "a.py", "two\n")
        rc, out, err = run_cli(root, "owe", "c")
        self.assertEqual(rc, 0, err)
        head = git(root, "rev-parse", "--short=7", "HEAD").strip()
        self.assertIn(f"owed c → t@example.com (since {head})", out)

    def test_verify_clears_an_owed_claim(self):
        root = self.store()
        write(root, "a.py", "two\n")
        run_cli(root, "owe", "c", "--to", "bob@example.com")
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        text = (root / "claims" / "c.md").read_text()
        self.assertIn("status: verified", text)
        self.assertNotIn("owed_by", text)


if __name__ == "__main__":
    unittest.main()
```

`tests/test_resolve.py`:
```python
"""Two clones pin the same claim to different content; the merge conflicts on
the claim's pins; `claimlock resolve` keeps only a pin matching the merged
source, else hands the claim off as owed by the merger."""
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import TmpCase, claim_text, git, run_cli, write
from claimlock.merge import hunks

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


class Hunks(unittest.TestCase):
    def test_two_way_and_diff3(self):
        lines = ["a", "<<<<<<< HEAD", "ours", "||||||| base", "old", "=======", "theirs", ">>>>>>> x", "b"]
        [h] = hunks(lines)
        self.assertEqual((h.start, h.end, h.ours, h.theirs), (1, 7, ["ours"], ["theirs"]))

    def test_unterminated_is_an_error(self):
        with self.assertRaises(ValueError):
            hunks(["<<<<<<< HEAD", "x", "======="])


def _clone(bare, dest, email):
    subprocess.run(["git", "clone", "-q", str(bare), str(dest)], check=True, capture_output=True)
    for k, v in (("user.email", email), ("user.name", email.split("@")[0]),
                 ("commit.gpgsign", "false"), ("pull.rebase", "false")):
        git(dest, "config", k, v)


@NEED_GIT
class ResolveAfterMerge(TmpCase):
    def setUp(self):
        super().setUp()
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True, capture_output=True)
        self.a, self.b = self.tmp / "a", self.tmp / "b"
        _clone(bare, self.a, "amy@example.com")
        run_cli(self.a, "init")
        write(self.a, "src.py", "MAX = 1\n")
        write(self.a, "claims/c.md", claim_text("c", sources=("src.py",)))
        run_cli(self.a, "verify", "c")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "c")
        git(self.a, "push", "-q", "origin", "main")
        _clone(bare, self.b, "ben@example.com")

    def both_verify(self, a_content, b_content):
        write(self.a, "src.py", a_content)
        run_cli(self.a, "verify", "c")
        git(self.a, "commit", "-qam", "a")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "src.py", b_content)
        run_cli(self.b, "verify", "c")
        git(self.b, "commit", "-qam", "b")
        return subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.b, capture_output=True, text=True)

    def test_identical_verifications_merge_cleanly(self):
        pull = self.both_verify("MAX = 2\n", "MAX = 2\n")
        self.assertEqual(pull.returncode, 0, pull.stderr)
        self.assertEqual(run_cli(self.b, "check")[0], 0)

    def test_theirs_content_keeps_theirs_pin(self):
        self.assertNotEqual(self.both_verify("MAX = 2\n", "MAX = 3\n").returncode, 0)
        git(self.b, "checkout", "--theirs", "src.py")
        rc, out, err = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("KEPT", out)
        git(self.b, "add", "-A")
        rc, out, _ = run_cli(self.b, "check")
        self.assertEqual(rc, 0, out)

    def test_ours_content_keeps_ours_pin(self):
        self.both_verify("MAX = 2\n", "MAX = 3\n")
        git(self.b, "checkout", "--ours", "src.py")
        rc, out, _ = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0)
        self.assertIn("KEPT", out)
        git(self.b, "add", "-A")
        self.assertEqual(run_cli(self.b, "check")[0], 0)

    def test_new_content_makes_the_claim_owed_by_the_merger(self):
        self.both_verify("MAX = 2\n", "MAX = 3\n")
        write(self.b, "src.py", "MAX = 4\n")
        rc, out, _ = run_cli(self.b, "resolve")
        self.assertEqual(rc, 0)
        self.assertIn("OWED", out)
        text = (self.b / "claims" / "c.md").read_text()
        self.assertIn("status: owed", text)
        self.assertIn("ben@example.com", text)
        self.assertNotIn("<<<<<<<", text)

    def test_a_prose_conflict_is_left_for_a_person(self):
        write(self.a, "claims/c.md", (self.a / "claims" / "c.md").read_text().replace("The thing holds.", "Amy's wording."))
        git(self.a, "commit", "-qam", "a prose")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "claims/c.md", (self.b / "claims" / "c.md").read_text().replace("The thing holds.", "Ben's wording."))
        git(self.b, "commit", "-qam", "b prose")
        subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.b, capture_output=True)
        rc, out, _ = run_cli(self.b, "resolve")
        self.assertEqual(rc, 1)
        self.assertIn("LEFT", out)
        self.assertIn("<<<<<<<", (self.b / "claims" / "c.md").read_text())


if __name__ == "__main__":
    unittest.main()
```
Note: if a `git pull` in these tests leaves the claim conflict as a clean merge because git merged the pin lines trivially, the `assertNotEqual(… returncode, 0)` precondition will say so — that means the scenario did not reproduce, not that resolve works; fix the scenario, never the assertion.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_owe.py" -v` and `-p "test_resolve.py"`.
Expected: `invalid choice: 'owe'` / `'resolve'` (rc 2) and `ModuleNotFoundError: No module named 'claimlock.merge'`. `test_identical_verifications_merge_cleanly` should already pass after Task 4 (control for spec F1).

- [ ] **Step 3: Implement**

`lib/claimlock/gitio.py`:
```python
def user_email(root):
    return _text(run(root, "config", "user.email")) or None


def short_head(root):
    return _text(run(root, "rev-parse", "--short=7", "--verify", "-q", "HEAD")) or None
```
(`git config user.email` also works outside a repository — it reads global config — which is why `test_no_identity_outside_git_is_exit_2` isolates config.)

`lib/claimlock/ops.py` — add `from datetime import date` and `from . import gitio`:
```python
class NeedsIdentity(Exception):
    """No one to record: no --to and no git user.email. Exit 2."""


def owe(project, cid, to=None, reason=None, today=None):
    """Hand off a re-check: status owed, owed_by, owed_since. Refuses a claim
    with problems, an unverified/refuted claim, a fresh claim, and a claim
    already owed to the same person."""
    c = next((x for x in C.load_claims(project) if x.id == cid), None)
    if c is None:
        raise Refused(f"no claim {cid!r}")
    if c.conflicted or c.parse_error or C.problems(c, project):
        raise Refused(f"{cid} has problems that must be fixed first (run: claimlock check)")
    if c.status in ("unverified", "refuted"):
        raise Refused(f"{cid} is {c.status}; only a verified or owed claim can be owed")
    email = to or gitio.user_email(project.root)
    if not email:
        raise NeedsIdentity("nobody to hand this to — pass --to <email> or set git config user.email")
    if not C.EMAIL_RE.match(email):
        raise Refused(f"{email!r} is not an email address")
    if c.status == "verified":
        hasher = C.open_hasher(project)
        state, _ = C.freshness(c, project, hasher, C.anchors_for(project, [s.path for s in c.sources]))
        hasher.save()
        if state == "fresh":
            raise Refused(f"{cid} is fresh — nothing is owed")
    elif c.owed_by == email:
        raise Refused(f"{cid} is already owed by {email}")
    since = gitio.short_head(project.root) or "none"
    text = frontmatter.rewrite(c.text, c.path.name, status="owed",
                               set_fields={"owed_by": email, "owed_since": since})
    if reason:
        who = gitio.user_email(project.root) or "unknown"
        day = today or date.today().isoformat()
        text = text.rstrip("\n") + f"\n\nOwed {day} by {who}: {reason}\n"
    _write(c.path, text)
    return email, since
```
(`test_owe_a_stale_claim_to_someone` runs with `NO_IDENTITY` so the reason line reads `by unknown` regardless of the machine's global git config; `--to` is unaffected.)

`lib/claimlock/merge.py`:
```python
"""Resolve git merge conflicts in claim files — the `sources` pins only.

A pin is kept only when it equals the merged working-tree content of its
source, so every kept pin is exactly what one side verified. When a source
matches neither side, the claim becomes `owed` by whoever is merging.
Conflicts anywhere else (prose, evidence, other fields) are left for a person,
because no rule can decide them.
"""
import re
from dataclasses import dataclass

from . import claims as C
from . import frontmatter, gitio
from .project import safe_source

START, BASE, MID, END = "<<<<<<< ", "||||||| ", "=======", ">>>>>>> "
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")
_SOURCE_LINE = re.compile(r"^(sources:.*|  - .*|    [A-Za-z_][A-Za-z0-9_]*:.*|\s*)$")


@dataclass
class Hunk:
    start: int
    end: int
    ours: list
    theirs: list


def hunks(lines):
    """Conflict hunks, supporting diff3 base sections. ValueError when unbalanced."""
    out, i, n = [], 0, len(lines)
    while i < n:
        if lines[i].startswith(START):
            start, ours, theirs, part = i, [], [], "ours"
            i += 1
            while i < n and not lines[i].startswith(END):
                line = lines[i]
                if line.startswith(BASE):
                    part = "base"
                elif line == MID:
                    part = "theirs"
                elif part == "ours":
                    ours.append(line)
                elif part == "theirs":
                    theirs.append(line)
                i += 1
            if i == n:
                raise ValueError("unterminated conflict hunk")
            out.append(Hunk(start, i, ours, theirs))
        elif lines[i].startswith(END):
            raise ValueError(f"conflict end marker without a start (line {i + 1})")
        i += 1
    return out


def _side(lines, hs, which):
    out, i = [], 0
    for h in hs:
        out.extend(lines[i:h.start])
        out.extend(h.ours if which == "ours" else h.theirs)
        i = h.end + 1
    out.extend(lines[i:])
    return "\n".join(out)


def _in_sources(lines, hs, h):
    if not all(_SOURCE_LINE.match(line) for line in h.ours + h.theirs):
        return False
    if any(line.startswith("sources:") for line in h.ours + h.theirs):
        return True
    inside = {k for other in hs for k in range(other.start, other.end + 1)}
    j = h.start - 1
    while j >= 0:
        if j not in inside:
            line = lines[j]
            if line == "---":
                return False
            if _KEY.match(line):
                return line.startswith("sources:")
        j -= 1
    return False


def _pins(text, name):
    fm, _ = frontmatter.split(text, name)
    meta = frontmatter.parse(fm, name)
    out = []
    for e in meta.get("sources") or []:
        if isinstance(e, dict) and isinstance(e.get("path"), str):
            out.append((e["path"], e.get("blob") if isinstance(e.get("blob"), str) else None))
        elif isinstance(e, str):
            out.append((e, None))
    return out


def resolve_claim(project, claim):
    """("kept" | "owed" | "left", message). Writes the claim only for kept/owed."""
    name = claim.path.name
    lines = claim.text.split("\n")
    try:
        hs = hunks(lines)
    except ValueError as e:
        return "left", f"{name}: {e}"
    if not hs:
        return "left", f"{name}: no conflict markers"
    for h in hs:
        if not _in_sources(lines, hs, h):
            return "left", f"{name}: a conflict outside the sources block (line {h.start + 1}) needs a person"
    ours_text, theirs_text = _side(lines, hs, "ours"), _side(lines, hs, "theirs")
    try:
        ours, theirs = _pins(ours_text, name), _pins(theirs_text, name)
    except frontmatter.FrontmatterError as e:
        return "left", str(e)
    if sorted(p for p, _ in ours) != sorted(p for p, _ in theirs):
        return "left", f"{name}: the two sides cite different sources — resolve by hand"
    theirs_pin = dict(theirs)
    hasher = C.open_hasher(project)
    picked, all_match = [], True
    for path, ours_pin in ours:
        cur = hasher.blob(path, use_cache=False) if safe_source(project.root, path) else None
        if cur is not None and cur == ours_pin:
            picked.append({"path": path, "blob": ours_pin})
        elif cur is not None and cur == theirs_pin.get(path):
            picked.append({"path": path, "blob": theirs_pin[path]})
        else:
            all_match = False
            picked.append({"path": path, "blob": ours_pin} if ours_pin else {"path": path})
    if all_match:
        text = frontmatter.rewrite(ours_text, name, sources=picked)
        outcome, message = "kept", "every source matches one side's verified pin"
    else:
        email = gitio.user_email(project.root)
        if not email:
            return "left", (f"{name}: a source matches neither side, and there is no git user.email "
                            f"to record who owes the re-check — set one, then run claimlock resolve")
        since = gitio.short_head(project.root) or "none"
        text = frontmatter.rewrite(ours_text, name, status="owed", sources=picked,
                                   set_fields={"owed_by": email, "owed_since": since})
        outcome, message = "owed", f"a source matches neither side — owed by {email}"
    from .ops import _write
    _write(claim.path, text)
    return outcome, message
```

`lib/claimlock/cli.py` — add `from . import merge`, and:
```python
def cmd_owe(args):
    project = _project(args)
    rc = 0
    for cid in args.ids:
        try:
            by, since = ops.owe(project, cid, to=args.to, reason=args.reason)
        except ops.Refused as e:
            print(f"claimlock: {e}", file=sys.stderr)
            rc = 1
            continue
        print(f"owed {cid} → {by} (since {since})")
    return rc


def cmd_resolve(args):
    project = _project(args)
    claims = C.load_claims(project)
    by_id = {c.id: c for c in claims}
    rc = 0
    for cid in args.ids:
        if cid not in by_id:
            print(f"claimlock: no claim {cid!r}", file=sys.stderr)
            rc = 1
    targets = [by_id[i] for i in args.ids if i in by_id] if args.ids else [c for c in claims if c.conflicted]
    if not targets and not args.ids:
        print("claimlock: no conflicted claims")
    for c in targets:
        if not c.conflicted:
            print(f"-      {c.id}  not conflicted")
            continue
        outcome, message = merge.resolve_claim(project, c)
        print(f"{outcome.upper():6} {c.id}  {message}")
        if outcome == "left":
            rc = 1
    return rc
```
Register after `verify`:
```python
    p = add("owe", cmd_owe, "hand off a claim's re-check to someone (status: owed)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--to", help="email of who owes it (default: git config user.email)")
    p.add_argument("--reason", help="one line appended to the claim body")
    p = add("resolve", cmd_resolve, "settle conflicted pins after a merge")
    p.add_argument("ids", nargs="*")
```
In `main`, add before the `ops.Refused` handler:
```python
    except ops.NeedsIdentity as e:
        print(f"claimlock: {e}", file=sys.stderr)
        return 2
```

`README.md` — add rows for `claimlock owe` and `claimlock resolve` to the commands table (one line each, matching the help text above).

- [ ] **Step 4: Run to verify they pass**

Run both focused files, then the full suite plain and with `-W error::ResourceWarning`. Report the observed total (+12 tests).

- [ ] **Step 5: Watch resolve's safety rule fail for its own reason**

(a) In `resolve_claim` temporarily change `elif cur is not None and cur == theirs_pin.get(path):` to `elif theirs_pin.get(path):` (keep theirs whatever the content); `test_new_content_makes_the_claim_owed_by_the_merger` fails with KEPT. Re-apply.
(b) Temporarily make `_in_sources` return True unconditionally; `test_a_prose_conflict_is_left_for_a_person` fails. Re-apply. Suite green.

- [ ] **Step 6: Commit**

```bash
git add lib/claimlock/gitio.py lib/claimlock/ops.py lib/claimlock/merge.py lib/claimlock/cli.py README.md tests/test_owe.py tests/test_resolve.py
git commit -m "feat: claimlock owe hands off a re-check; claimlock resolve settles conflicted pins" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
