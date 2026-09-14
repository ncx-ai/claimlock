### Task 8: Hooks and plugin manifests

**Precondition:** read `docs/hook-semantics.md` (Task 1). If F1–F4 are not all CONFIRMED, stop and report — the output fields below depend on them.

**Files:**
- Create: `lib/claimlock/hooks.py`, `hooks/hooks.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `tests/test_hooks.py`, `tests/test_plugin_manifest.py`
- Modify: `lib/claimlock/cli.py` (add `cmd_hook`, register `hook`)

**Interfaces:**
- Consumes: `project.load`, `claims.*`, `refs.scan(project, only=)`, `gitio.head/changed_paths/head_mark_paths` (Tasks 3–7).
- Produces: `hooks.main(event, stdin_text, env) -> int` (always 0); events `session-start`, `stop`, `post-tool-use`. Session state at `<CLAUDE_PLUGIN_DATA or .claimlock>/sessions/<session_id>.json`; errors at `<same dir>/hook-errors.log`.

- [ ] **Step 1: Write the failing tests**

`tests/test_hooks.py`:
```python
import json
import shutil
import subprocess
import unittest

from helpers import TmpCase, claim_text, git, make_repo, run_cli, write

NEED_GIT = unittest.skipIf(shutil.which("git") is None, "git not installed")


class HookCase(TmpCase):
    def setUp(self):
        super().setUp()
        self.data = self.tmp / "plugin-data"

    def hook(self, root, event, session="s1", stdin=None):
        payload = stdin if stdin is not None else json.dumps({"session_id": session, "cwd": str(root)})
        rc, out, err = run_cli(root, "hook", event, stdin=payload,
                               env={"CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_DATA": str(self.data)})
        self.assertEqual(rc, 0, f"hooks must always exit 0; stderr={err}")
        return json.loads(out) if out.strip() else None

    def store(self, use_git=False):
        root = make_repo(self.tmp / "r", use_git=use_git)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "one\n")
        write(root, "claims/c.md", claim_text("c", area="api", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)
        if use_git:
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "init")
        return root


class Contract(HookCase):
    def test_inert_without_a_store(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        for event in ("session-start", "stop", "post-tool-use"):
            self.assertIsNone(self.hook(plain, event))
        self.assertIsNone(self.hook(plain, "stop", stdin="not json"))
        self.assertFalse(self.data.exists())

    def test_internal_errors_are_logged_not_shown(self):
        root = make_repo(self.tmp / "r", use_git=False, config="bogus = 1\n")
        self.assertIsNone(self.hook(root, "session-start"))
        self.assertIn("unknown key", (self.data / "hook-errors.log").read_text())
        good = self.store()
        self.assertIsNone(self.hook(good, "no-such-event"))
        self.assertIn("unknown hook event", (self.data / "hook-errors.log").read_text())


class SessionStart(HookCase):
    def test_reports_counts_and_areas_to_claude(self):
        root = self.store()
        out = self.hook(root, "session-start")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("1 claims, all fresh", ctx)
        write(root, "a.py", "two!\n")
        ctx = self.hook(root, "session-start", session="s2")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("1 stale", ctx)
        self.assertIn("api (1)", ctx)
        self.assertNotIn("decision", json.dumps(out))
        self.assertTrue((self.data / "sessions" / "s2.json").is_file())

    def test_output_is_bounded(self):
        root = make_repo(self.tmp / "big", use_git=False)
        for i in range(400):
            write(root, f"claims/claim-{i:03d}-with-a-long-identifier.md",
                  claim_text(f"claim-{i:03d}-with-a-long-identifier", status="maybe", area=f"area-{i}"))
        ctx = self.hook(root, "session-start")["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 2000)
        self.assertIn("400 invalid", ctx)


class Stop(HookCase):
    def test_warns_user_only_about_new_problems_once(self):
        root = self.store()
        write(root, "claims/old.md", claim_text("old", status="maybe"))  # pre-existing problem
        self.hook(root, "session-start")
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "two!\n")
        out = self.hook(root, "stop")
        self.assertEqual(set(out), {"systemMessage"})
        self.assertIn("introduced 1 stale (c)", out["systemMessage"])
        self.assertNotIn("old", out["systemMessage"])
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "one\n")
        self.assertIsNone(self.hook(root, "stop"))
        write(root, "a.py", "three\n")
        self.assertIn("introduced 1 stale (c)", self.hook(root, "stop")["systemMessage"])

    def test_no_baseline_means_no_warning(self):
        root = self.store()
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "stop", session="fresh-session"))
        self.assertIsNone(self.hook(root, "stop", session="fresh-session"))


@NEED_GIT
class HeadMovement(HookCase):
    def test_commit_by_any_means_is_reported_to_claude(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        self.assertIsNone(self.hook(root, "post-tool-use"))
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "post-tool-use"))  # an uncommitted edit is Stop's business
        write(root, "ship.sh", "#!/bin/sh\ngit add -A && git commit -q -m ship\n")
        subprocess.run(["sh", "ship.sh"], cwd=root, check=True, capture_output=True)
        out = self.hook(root, "post-tool-use")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("HEAD moved", ctx)
        self.assertIn("c (stale)", ctx)
        self.assertIsNone(self.hook(root, "post-tool-use"))

    def test_reset_backwards_is_reported(self):
        # Claims are tracked, so a reset restores a claim together with its
        # source. To get drift from a backwards move, commit 1 must hold a pin
        # that only matches commit 2's content.
        root = make_repo(self.tmp / "r", use_git=True)
        write(root, ".gitignore", ".claimlock/\n")
        write(root, "a.py", "two!\n")
        write(root, "claims/c.md", claim_text("c", area="api", sources=("a.py",)))
        self.assertEqual(run_cli(root, "verify", "c")[0], 0)  # pinned to "two!"
        write(root, "a.py", "one\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "c1: pin ahead of content")
        write(root, "a.py", "two!\n")
        git(root, "commit", "-qam", "c2: content catches up")
        self.assertEqual(run_cli(root, "check")[0], 0)
        self.hook(root, "session-start")
        git(root, "reset", "-q", "--hard", "HEAD~1")
        ctx = self.hook(root, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("c (stale)", ctx)

    def test_dangling_marker_committed(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        write(root, "docs/x.md", "Claim: `ghost`\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "docs")
        ctx = self.hook(root, "post-tool-use")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("docs/x.md:ghost", ctx)

    def test_stop_also_catches_commits_made_outside_tools(self):
        root = self.store(use_git=True)
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")
        git(root, "commit", "-qam", "from another terminal")
        msg = self.hook(root, "stop")["systemMessage"]
        self.assertIn("HEAD moved", msg)


class NotGit(HookCase):
    def test_post_tool_use_is_silent_outside_git(self):
        root = self.store(use_git=False)
        self.hook(root, "session-start")
        write(root, "a.py", "two!\n")
        self.assertIsNone(self.hook(root, "post-tool-use"))


if __name__ == "__main__":
    unittest.main()
```

`tests/test_plugin_manifest.py`:
```python
import json
import os
import unittest

from helpers import REPO


class Manifest(unittest.TestCase):
    def test_plugin_marketplace_and_hooks_agree(self):
        plugin = json.loads((REPO / ".claude-plugin/plugin.json").read_text())
        market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual(plugin["name"], "claimlock")
        self.assertTrue(plugin["description"].startswith("Claude Code plugin:"))
        [entry] = market["plugins"]
        self.assertEqual((entry["name"], entry["source"]), ("claimlock", "./"))
        self.assertIn("name", market["owner"])
        hooks = json.loads((REPO / "hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "Stop", "PostToolUse"})
        commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
        for event in ("session-start", "stop", "post-tool-use"):
            self.assertTrue(any(c.endswith(f"hook {event}") for c in commands), event)
        self.assertTrue(all("${CLAUDE_PLUGIN_ROOT}/bin/claimlock" in c for c in commands))
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "Bash|mcp__.*")

    def test_launcher_is_executable(self):
        self.assertTrue(os.access(REPO / "bin/claimlock", os.X_OK))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: hook tests fail with `invalid choice: 'hook'` (rc 2 ≠ 0); manifest tests fail with `FileNotFoundError`.

- [ ] **Step 3: Implement `lib/claimlock/hooks.py`**

```python
"""Hook entry points: SessionStart, Stop, PostToolUse.

The contract is enforced here and nowhere else:
- always exit 0 and never set `decision`: a warning must never become a block;
- print nothing in a project that has no claim store;
- an internal error is appended to hook-errors.log and never shown to Claude.

Commits are detected by HEAD moving, not by matching a command, so `gh`, git
aliases, scripts, merges, rebases, pulls and MCP tools are all seen. The common
path is a handful of `stat` calls; git runs only when HEAD's files changed.
"""
import json
import os
import re
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import claims as C
from . import gitio, refs
from . import project as P

LIMIT = 2000
PROBE_EVERY_S = 30
RECHECK = ("Before asserting a limit, default or guarantee, run `claimlock search <topic>`. "
           "A non-fresh claim is owed a re-check (`claimlock diff <id>`), never a bare re-stamp.")


def main(event, stdin_text, env) -> int:
    data_dir = None
    try:
        payload = json.loads(stdin_text) if stdin_text and stdin_text.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        start = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
        project = P.load(start)
        if not project.claims_dir.is_dir():
            return 0
        data_dir = Path(env.get("CLAUDE_PLUGIN_DATA") or project.state_dir)
        handler = HANDLERS.get(event)
        if handler is None:
            raise ValueError(f"unknown hook event {event!r}")
        out = handler(project, payload, data_dir)
        if out:
            print(json.dumps(out))
    except Exception:  # noqa: BLE001 — a hook must never fail loudly
        _log(data_dir or env.get("CLAUDE_PLUGIN_DATA"), event)
    return 0


def _log(where, event):
    try:
        d = Path(where) if where else Path(tempfile.gettempdir()) / "claimlock"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hook-errors.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()} {event}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def _state_path(data_dir, payload):
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "no-session"))
    return data_dir / "sessions" / f"{sid}.json"


def _load_state(path, project):
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) and st.get("root") == str(project.root) else None


def _save_state(path, st):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.replace(tmp, path)


def survey(project):
    hasher = C.open_hasher(project)
    results = C.evaluate(project, hasher)
    hasher.save()
    ids = {r.claim.id for r in results}
    markers, _ = refs.scan(project)
    s = {"invalid": sorted(r.claim.id for r in results if r.problems)}
    for k in C.NON_FRESH:
        s[k] = sorted(r.claim.id for r in results if r.state == k)
    s["dangling"] = sorted({f"{m.path}:{m.id}" for m in markers if m.id not in ids})
    return s, results


def _stat_marks(paths):
    marks = {}
    for p in paths:
        try:
            marks[p] = os.stat(p).st_mtime_ns
        except OSError:
            marks[p] = None
    return marks


def _init_head(project, st):
    paths = gitio.head_mark_paths(project.root)
    st["mark_paths"] = paths
    st["marks"] = _stat_marks(paths)
    st["last_head"] = gitio.head(project.root) if paths else None
    st["probed_at"] = time.time()


def _new_state(project):
    st = {"root": str(project.root), "baseline": survey(project)[0]}
    _init_head(project, st)
    return st


def head_check(project, st):
    """Describe claims left non-fresh by commits since the last check, or None. Mutates `st`."""
    paths = st.get("mark_paths") or []
    if not paths:
        if time.time() - st.get("probed_at", 0) >= PROBE_EVERY_S:
            _init_head(project, st)  # a repository may have been created since
        return None
    marks = _stat_marks(paths)
    if marks == st.get("marks"):
        return None
    old, new = st.get("last_head"), gitio.head(project.root)
    _init_head(project, st)  # the checked-out branch, and so the ref file, may have changed
    if new is None or new == old:
        return None
    changed = set(gitio.changed_paths(project.root, old, new))
    hasher = C.open_hasher(project)
    results = C.evaluate(project, hasher)
    hasher.save()
    hit = [(r.claim.id, r.state) for r in results
           if r.state in C.NON_FRESH and any(s.path in changed for s in r.claim.sources)]
    ids = {r.claim.id for r in results}
    markers, _ = refs.scan(project, only=changed)
    dangling = sorted({f"{m.path}:{m.id}" for m in markers if m.id not in ids})
    if not hit and not dangling:
        return None
    parts = [f"claimlock: HEAD moved {(old or 'none')[:7]}→{new[:7]} (a commit, merge, rebase, pull or checkout)."]
    if hit:
        more = "…" if len(hit) > 10 else ""
        parts.append(f"Files changed in that range back {len(hit)} claim(s) that are no longer fresh: "
                     + ", ".join(f"{i} ({s})" for i, s in hit[:10]) + more + ".")
    if dangling:
        parts.append("Markers naming no claim: " + ", ".join(dangling[:10]) + ".")
    parts.append("Re-check each with `claimlock diff <id>`; `claimlock verify <id>` only after re-checking.")
    return " ".join(parts)


def session_start(project, payload, data_dir):
    s, results = survey(project)
    st = {"root": str(project.root), "baseline": s}
    _init_head(project, st)
    _save_state(_state_path(data_dir, payload), st)
    n = len(results)
    if not any(s.values()):
        text = f"claimlock: {n} claims, all fresh. {RECHECK}"
    else:
        counts = ", ".join(f"{len(v)} {k}" for k, v in s.items())
        areas = Counter(r.claim.area for r in results if r.problems or r.state in C.NON_FRESH)
        area_line = ("Affected areas: " + ", ".join(f"{a} ({c})" for a, c in areas.most_common(8))
                     + (", …" if len(areas) > 8 else "") + ".\n") if areas else ""
        text = f"claimlock: {n} claims — {counts}.\n{area_line}{RECHECK}"
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text[:LIMIT]}}


def stop(project, payload, data_dir):
    path = _state_path(data_dir, payload)
    st = _load_state(path, project)
    if st is None:
        _save_state(path, _new_state(project))
        return None
    s, _ = survey(project)
    base = st.get("baseline") or {}
    new = {k: [x for x in v if x not in set(base.get(k, []))] for k, v in s.items()}
    head_msg = head_check(project, st)
    st["baseline"] = s
    _save_state(path, st)
    parts = []
    if any(new.values()):
        parts.append("claimlock: this session introduced "
                     + "; ".join(f"{len(v)} {k} ({', '.join(v[:5])}{'…' if len(v) > 5 else ''})"
                                 for k, v in new.items() if v)
                     + ". Inspect with `claimlock diff <id>` or `claimlock refs`.")
    if head_msg:
        parts.append(head_msg)
    return {"systemMessage": "\n".join(parts)[:LIMIT]} if parts else None


def post_tool_use(project, payload, data_dir):
    path = _state_path(data_dir, payload)
    st = _load_state(path, project)
    if st is None:
        _save_state(path, _new_state(project))
        return None
    before = json.dumps(st, sort_keys=True)
    msg = head_check(project, st)
    if json.dumps(st, sort_keys=True) != before:
        _save_state(path, st)
    if not msg:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg[:LIMIT]}}


HANDLERS = {"session-start": session_start, "stop": stop, "post-tool-use": post_tool_use}
```

Note `test_inert_without_a_store` asserts the data dir is not created: `main` returns before `data_dir` is touched when there is no claims directory. A project with a config but no claims dir is also inert (the gate, not the hook, reports that).

Note `test_internal_errors_are_logged_not_shown`: the bad-config project raises `ConfigError` inside `P.load`, before `data_dir` is set, so `_log` falls back to `env["CLAUDE_PLUGIN_DATA"]`.

- [ ] **Step 4: Register the command in `lib/claimlock/cli.py`**

Add import `from . import hooks`. Add:
```python
def cmd_hook(args):
    return hooks.main(args.event, sys.stdin.read(), dict(os.environ))
```
Register after `self-test`:
```python
    p = add("hook", cmd_hook, "Claude Code hook entry point (always exits 0)")
    p.add_argument("event")
```

- [ ] **Step 5: Plugin wiring**

`hooks/hooks.json` (use the invocation form Task 1 confirmed; this is the expected one):
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/bin/claimlock\" hook session-start", "timeout": 60}]
      }
    ],
    "Stop": [
      {
        "hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/bin/claimlock\" hook stop", "timeout": 60}]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash|mcp__.*",
        "hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/bin/claimlock\" hook post-tool-use", "timeout": 30}]
      }
    ]
  }
}
```

`.claude-plugin/plugin.json`:
```json
{
  "name": "claimlock",
  "displayName": "claimlock",
  "version": "0.1.0",
  "description": "Claude Code plugin: pin claims about your code to the content that could falsify them. A CLI gate, drift-aware hooks, and skills for evidence discipline.",
  "author": {"name": "dg"},
  "license": "MIT",
  "keywords": ["claims", "documentation", "drift", "staleness", "evidence", "verification", "skills", "hooks"]
}
```

`.claude-plugin/marketplace.json`:
```json
{
  "name": "claimlock",
  "owner": {"name": "dg"},
  "description": "claimlock — claims pinned to the content that could falsify them.",
  "plugins": [
    {
      "name": "claimlock",
      "source": "./",
      "description": "CLI gate, drift-aware hooks, and evidence-discipline skills for keeping written claims about code honest.",
      "tags": ["documentation", "quality", "verification"]
    }
  ]
}
```

- [ ] **Step 6: Run to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK`; new tests: Contract 2, SessionStart 2, Stop 2, HeadMovement 4, NotGit 1, Manifest 2 = 13; total `Ran 77 tests`. Report skips.

- [ ] **Step 7: Watch the non-blocking contract and the baseline fail for their own reasons**

(a) In `main`, change `return 0` at the very end to `return 2`; run; every `HookCase` test FAILS with `hooks must always exit 0`. Re-apply the inverse edit.
(b) In `stop`, change `st["baseline"] = s` to `pass`; run; `test_warns_user_only_about_new_problems_once` FAILS at the repeated-Stop assertion. Re-apply the inverse edit. Suite OK.

- [ ] **Step 8: Measure the common-path cost**

The claimlock repo itself has no store, so measuring there would time the inert path. Build a scratch store with a baseline first, then time the common path (HEAD unchanged):
```bash
B=$(mktemp -d) && cd "$B" && git init -q && git config user.email b@b && git config user.name b \
  && <claimlock-repo>/bin/claimlock init >/dev/null && echo x > a.py \
  && git add -A && git commit -qm init
P='{"session_id":"bench"}'
echo "$P" | CLAUDE_PROJECT_DIR=$B CLAUDE_PLUGIN_DATA=$B/data <claimlock-repo>/bin/claimlock hook session-start >/dev/null
test -f "$B/data/sessions/bench.json" || echo "BASELINE NOT WRITTEN — the timing below would be meaningless"
for i in $(seq 10); do /usr/bin/time -f %e sh -c "echo '$P' | CLAUDE_PROJECT_DIR=$B CLAUDE_PLUGIN_DATA=$B/data <claimlock-repo>/bin/claimlock hook post-tool-use >/dev/null"; done
```
Record the median in the commit message body. If it exceeds 150 ms, report DONE_WITH_CONCERNS with the number.

- [ ] **Step 9: Commit**

```bash
git add lib/claimlock hooks .claude-plugin tests/test_hooks.py tests/test_plugin_manifest.py
git commit -m "feat: non-blocking hooks — session baseline, new-drift warnings, HEAD-movement detection" -m "post-tool-use common path median: <N> ms

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
