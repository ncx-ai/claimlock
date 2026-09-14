### Task 7: Hooks with a team

Spec §6. The hooks already report "since the last check" and dedupe against the HEAD-moved report. Now: SessionStart says what is owed to you; a HEAD move names the commit and author that changed a claim's source, the hand-offs that arrived for you, and merge conflicts in claim files; Stop separates drift from your uncommitted edits. All v1 hook guarantees stand, and PostToolUse with HEAD unchanged still runs no git.

**Files:**
- Modify: `lib/claimlock/gitio.py` (add `range_log`, `dirty_paths`), `lib/claimlock/hooks.py` (`survey`, `_new_state`, `session_start`, `head_check`, `_head_report`, `stop`), `tests/helpers.py` (hoist `init_bare`, `clone`)
- Modify tests: `tests/test_hooks.py` (new classes), `tests/test_resolve.py`, `tests/test_hashing.py`, `tests/test_collaboration.py` (use the hoisted helpers; behaviour identical)

**Interfaces:**
- Consumes: `claims.Claim.conflicted/owed_by`, `claims.NON_FRESH`, `gitio.user_email/changed_paths` (Tasks 2–6).
- Produces: `gitio.range_log(root, old, new) -> [(sha7, email, subject, [paths])]` newest first; `gitio.dirty_paths(root) -> [paths]`; `helpers.init_bare(path)`, `helpers.clone(bare, dest, email="t@example.com", *git_config)`.

- [ ] **Step 1: Hoist the clone helpers (no behaviour change)**

Add to `tests/helpers.py`:
```python
def init_bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(path)], check=True, capture_output=True)
    return path


def clone(bare: Path, dest: Path, email="t@example.com", *git_config) -> Path:
    """Clone with a local identity, unsigned commits and merge-on-pull.
    `git_config` entries like "core.autocrlf=true" apply to the clone command."""
    pre = [a for kv in git_config for a in ("-c", kv)]
    subprocess.run(["git", *pre, "clone", "-q", str(bare), str(dest)], check=True, capture_output=True)
    for k, v in (("user.email", email), ("user.name", email.split("@")[0]),
                 ("commit.gpgsign", "false"), ("pull.rebase", "false")):
        git(dest, "config", k, v)
    for kv in git_config:
        git(dest, "config", *kv.split("=", 1))
    return dest
```
Replace the local clone/bare helpers in `tests/test_resolve.py`, `tests/test_hashing.py` and `tests/test_collaboration.py` with these (in `test_hashing.py`, `clone(bare, b, "t@example.com", "core.autocrlf=true")`). Run those three files: same pass counts as before.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_hooks.py` (add `init_bare, clone` to its helpers import, and `import os, stat, subprocess` if missing):
```python
@NEED_GIT
class TeamHooks(HookCase):
    def setUp(self):
        super().setUp()
        bare = init_bare(self.tmp / "origin.git")
        self.a = clone(bare, self.tmp / "a", "amy@example.com")
        run_cli(self.a, "init")
        write(self.a, "src.py", "MAX = 1\n")
        write(self.a, "claims/c.md", claim_text("c", sources=("src.py",)))
        run_cli(self.a, "verify", "c")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "c")
        git(self.a, "push", "-q", "origin", "main")
        self.b = clone(bare, self.tmp / "b", "ben@example.com")

    def pull_b(self):
        return subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=self.b, capture_output=True, text=True)

    def test_a_pull_names_who_changed_a_claims_source(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 9\n")
        git(self.a, "commit", "-qam", "raise MAX")
        git(self.a, "push", "-q", "origin", "main")
        self.pull_b()
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn('c (stale): src.py changed by amy@example.com in', ctx)
        self.assertIn('"raise MAX"', ctx)

    def test_a_hand_off_to_you_is_reported_on_pull_and_at_session_start(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 9\n")
        git(self.a, "commit", "-qam", "raise MAX")
        run_cli(self.a, "owe", "c", "--to", "ben@example.com")
        git(self.a, "commit", "-qam", "owe c to ben")
        git(self.a, "push", "-q", "origin", "main")
        self.pull_b()
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Now owed to you: c", ctx)
        ctx = self.hook(self.b, "session-start", session="s2")["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(ctx.startswith("claimlock: owed to you: 1 (c)"), ctx)

    def test_a_conflicted_merge_says_run_resolve(self):
        self.hook(self.b, "session-start")
        write(self.a, "src.py", "MAX = 2\n")
        run_cli(self.a, "verify", "c")
        git(self.a, "commit", "-qam", "a")
        git(self.a, "push", "-q", "origin", "main")
        write(self.b, "src.py", "MAX = 3\n")
        run_cli(self.b, "verify", "c")
        git(self.b, "commit", "-qam", "b")
        self.assertNotEqual(self.pull_b().returncode, 0, "precondition: the merge conflicts")
        # A conflicted merge does not move HEAD; the merge finishes when committed.
        git(self.b, "checkout", "--theirs", "src.py")
        git(self.b, "add", "-A")   # stages the marker-bearing claim too; git refuses to commit unmerged paths
        git(self.b, "commit", "-qm", "merge, claim still conflicted", "--no-verify")
        ctx = self.hook(self.b, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("run `claimlock resolve`", ctx)

    def test_stop_separates_your_uncommitted_edits(self):
        self.hook(self.b, "session-start")
        write(self.b, "src.py", "MAX = 5\n")
        msg = self.hook(self.b, "stop")["systemMessage"]
        self.assertIn("from your uncommitted edits, 1 claim became stale (c)", msg)
        self.assertNotIn("since the last check", msg)


@NEED_GIT
class CommonPathRunsNoGit(HookCase):
    def test_post_tool_use_with_head_unchanged_invokes_no_git(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        fake = self.tmp / "fakebin"
        fake.mkdir()
        calls = self.tmp / "git-calls"
        (fake / "git").write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\nexit 1\n")
        os.chmod(fake / "git", 0o755)
        payload = json.dumps({"session_id": "s1", "cwd": str(root)})
        rc, out, err = run_cli(root, "hook", "post-tool-use", stdin=payload,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(self.data),
                                    "PATH": str(fake)})
        self.assertEqual((rc, out), (0, ""), err)
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")
```
Note for `test_a_conflicted_merge_says_run_resolve`: committing a file that still contains conflict markers is deliberate — it reproduces a merge someone finished without running `resolve`, which is exactly when the notice matters.

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_hooks.py" -v`
Expected: the four `TeamHooks` tests fail on their message substrings. `CommonPathRunsNoGit` should already pass — it is the regression guard; confirm it can fail in Step 6.

- [ ] **Step 4: Implement**

`lib/claimlock/gitio.py`:
```python
def range_log(root, old, new):
    """[(short sha, author email, subject, [root-relative paths])], newest first,
    for commits in old..new — or just `new` when `old` is None or unreachable."""
    fmt = "--format=%x00%h%x09%ae%x09%s"
    r = run(root, "log", "--no-renames", "--name-only", "--relative", fmt, f"{old}..{new}") if old else None
    if r is None or r.returncode != 0:
        r = run(root, "log", "-1", "--no-renames", "--name-only", "--relative", fmt, new)
    if r is None or r.returncode != 0:
        return []
    out = []
    for chunk in r.stdout.decode("utf-8", "replace").split("\0")[1:]:
        lines = [line for line in chunk.splitlines() if line]
        if not lines:
            continue
        head = lines[0].split("\t", 2)
        if len(head) == 3:
            out.append((head[0], head[1], head[2], lines[1:]))
    return out


def dirty_paths(root):
    """Tracked paths whose working-tree content differs from HEAD, root-relative."""
    out = _text(run(root, "diff", "--name-only", "--relative", "HEAD"))
    return _lines(out) if out is not None else []
```

`lib/claimlock/hooks.py`:

Add a helper:
```python
def _claim_rel(project, claim):
    try:
        return claim.path.relative_to(project.root).as_posix()
    except ValueError:
        return None
```

`survey` — replace the dict construction with:
```python
    s = {"invalid": sorted(r.claim.id for r in results if r.problems and not r.claim.conflicted),
         "conflicted": sorted(r.claim.id for r in results if r.claim.conflicted)}
    for k in C.NON_FRESH:
        s[k] = sorted(r.claim.id for r in results if r.state == k)
    s["owed"] = sorted(r.claim.id for r in results if r.claim.status == "owed")
    s["dangling"] = sorted({f"{m.path}:{m.id}" for m in markers if m.id not in ids})
```

Record the identity whenever HEAD tracking is (re)initialised in a git work tree: at the end of `_init_head`, add
```python
    if paths and "email" not in st:
        st["email"] = gitio.user_email(project.root)
```

`session_start` — after `_init_head(project, st)` and before building `text`, compute:
```python
    me = st.get("email")
    mine = sorted(r.claim.id for r in results if me and r.claim.status == "owed" and r.claim.owed_by == me)
    lead = (f"claimlock: owed to you: {len(mine)} ({', '.join(mine[:10])}{'…' if len(mine) > 10 else ''}).\n"
            if mine else "")
```
and prefix it: `text = lead + text` before the `[:LIMIT]` return.

`head_check` — after `changed = …` and the `results = C.evaluate(...)` / `hasher.save()` lines, replace the `hit = …` computation through the `return` with:
```python
    who = {}
    for sha, email, subject, paths in gitio.range_log(project.root, old, new):
        for p in paths:
            who.setdefault(p, (sha, email, subject))
    hit = []
    for r in results:
        if r.state in C.NON_FRESH:
            src = next((s.path for s in r.claim.sources if s.path in changed), None)
            if src is not None:
                hit.append((r.claim.id, r.state, src, who.get(src)))
    me = st.get("email")
    owed_new = sorted(r.claim.id for r in results
                      if me and r.claim.status == "owed" and r.claim.owed_by == me
                      and _claim_rel(project, r.claim) in changed)
    conflicted = sorted(r.claim.id for r in results if r.claim.conflicted)
    ids = {r.claim.id for r in results}
    markers, _ = refs.scan(project, only=changed)
    dangling = sorted({f"{m.path}:{m.id}" for m in markers if m.id not in ids})
    if not (hit or dangling or owed_new or conflicted):
        return None
    return _head_report(old, new, hit, dangling, owed_new, conflicted)
```

Replace `_head_report`:
```python
def _head_report(old, new, hit, dangling, owed_new=(), conflicted=()):
    parts = [f"claimlock: HEAD moved {(old or 'none')[:7]}→{new[:7]} (a commit, merge, rebase, pull or checkout)."]
    if hit:
        more = "…" if len(hit) > 10 else ""
        entries = [f'{cid} ({state}): {src} changed by {w[1]} in {w[0]} "{w[2]}"' if w else f"{cid} ({state})"
                   for cid, state, src, w in hit[:10]]
        parts.append(f"Files changed in that range back {len(hit)} claim(s) that are no longer fresh: "
                     + "; ".join(entries) + more + ".")
    if owed_new:
        parts.append("Now owed to you: " + ", ".join(owed_new[:10]) + ("…" if len(owed_new) > 10 else "") + ".")
    if conflicted:
        parts.append(f"{len(conflicted)} claim file(s) have merge conflicts ({', '.join(conflicted[:5])}"
                     f"{'…' if len(conflicted) > 5 else ''}) — run `claimlock resolve`.")
    if dangling:
        more = "…" if len(dangling) > 10 else ""
        parts.append("Markers naming no claim: " + ", ".join(dangling[:10]) + more + ".")
    parts.append("Re-check each with `claimlock diff <id>`; `claimlock verify <id>` only after re-checking.")
    report = _HeadReport(" ".join(parts))
    report.named = frozenset([h[0] for h in hit[:10]] + list(dangling[:10])
                             + list(owed_new[:10]) + list(conflicted[:5]))
    return report
```

`stop` — change `s, _ = survey(project)` to `s, results = survey(project)`, and replace the block that builds `parts` from `new` with:
```python
    dirty = set(gitio.dirty_paths(project.root)) if st.get("mark_paths") else set()
    by_id = {r.claim.id: r for r in results}
    yours, others = {}, {}
    for kind, items in new.items():
        for item in items:
            r = by_id.get(item)
            mine = r is not None and dirty and any(sp.path in dirty for sp in r.claim.sources)
            (yours if mine else others).setdefault(kind, []).append(item)
    parts = []
    if yours:
        parts.append("claimlock: from your uncommitted edits, "
                     + "; ".join(_since_phrase(k, v) for k, v in yours.items())
                     + ". Inspect with `claimlock diff <id>`.")
    if others:
        parts.append("claimlock: since the last check, "
                     + "; ".join(_since_phrase(k, v) for k, v in others.items())
                     + ". Inspect with `claimlock diff <id>` or `claimlock refs`.")
    if head_msg:
        parts.append(head_msg)
```
(keep the existing `named` filtering of `new` above it, and the existing return).

- [ ] **Step 5: Run to verify they pass**

Run `tests/test_hooks.py`, then the full suite plain and with `-W error::ResourceWarning`. Existing hook tests that assert substrings of the old HEAD report (`c (stale)`, `docs/x.md:ghost`, length bounds) must pass unchanged; if one asserted the exact old `, `-joined list, update only that string to the new `; `-joined entry format and name it in the report. Report the observed total (+5 tests).

- [ ] **Step 6: Watch the new behaviour and the no-git guarantee fail for their own reasons**

(a) In `head_check` temporarily replace `who.get(src)` with `None`; `test_a_pull_names_who_changed_a_claims_source` fails. Re-apply.
(b) In `post_tool_use` temporarily add `gitio.in_git(project.root)` as its first line; `test_post_tool_use_with_head_unchanged_invokes_no_git` fails (the fake git records a call). Re-apply.
(c) In `stop` temporarily set `dirty = set()`; `test_stop_separates_your_uncommitted_edits` fails. Re-apply. Suite green.

- [ ] **Step 7: Commit**

```bash
git add lib/claimlock/gitio.py lib/claimlock/hooks.py tests/helpers.py tests/test_hooks.py tests/test_resolve.py tests/test_hashing.py tests/test_collaboration.py
git commit -m "feat(hooks): name who changed a claim's source, hand-offs owed to you, and merge conflicts" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
