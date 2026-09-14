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
        try:
            payload = json.loads(stdin_text) if stdin_text and stdin_text.strip() else {}
        except ValueError:
            # Malformed stdin degrades to an empty payload, same as absent
            # stdin — it must not be treated as an internal error: that would
            # touch hook-errors.log (and so the data dir) even in a project
            # with no claim store, contradicting "print nothing... in a
            # project that has no claim store" above.
            payload = {}
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
