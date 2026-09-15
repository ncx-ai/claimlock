"""Hook entry points: SessionStart, Stop, PostToolUse.

The contract is enforced here and nowhere else:
- always exit 0 and never set `decision`: a warning must never become a block;
- print nothing in a project that has no claim store;
- an internal error is appended to hook-errors.log and never shown to Claude.

Commits are detected by HEAD moving, not by matching a command, so `gh`, git
aliases, scripts, merges, rebases, pulls and MCP tools are all seen. The common
path is a handful of `stat` calls; git runs only when HEAD's files changed.
"""
import contextlib
import json
import os
import re
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (e.g. Windows): no locking available
    fcntl = None

from . import claims as C
from . import gitio, refs
from . import project as P

LIMIT = 2000
PROBE_EVERY_S = 30
LOCK_TIMEOUT_S = 5.0
LOCK_POLL_S = 0.05
_UNSET = object()
RECHECK = ("Before asserting a limit, default or guarantee, run `claimlock search <topic>`. "
           "A non-fresh claim is owed a re-check (`claimlock diff <id>`), never a bare re-stamp.")


def main(event, stdin_text, env) -> int:
    data_dir = None
    try:
        payload, malformed = _parse_payload(stdin_text)
        start = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
        if not _has_config(start):
            # Hooks are active only in a project that opted in with
            # .claimlock.toml. Decided with plain stats — no git, no data dir —
            # because PostToolUse runs this on every Bash and MCP call.
            return 0
        project = P.load(start)
        if not project.claims_dir.is_dir():
            return 0
        data_dir = Path(env.get("CLAUDE_PLUGIN_DATA") or project.state_dir)
        if malformed:
            # Only worth a note once we know there's a store to log into —
            # the storeless case above must stay completely silent.
            _log_note(data_dir, f"{event}: hook stdin was not valid JSON; payload treated as empty")
        handler = HANDLERS.get(event)
        if handler is None:
            raise ValueError(f"unknown hook event {event!r}")
        # Serialise this session's load -> check -> save: concurrent hook
        # processes for the same session (several PostToolUse calls in
        # flight, or PostToolUse racing Stop) must not each read the same
        # stale state, compute the same diff and report it N times, nor
        # interleave writes to the same state file.
        with _session_lock(data_dir, _sid(payload)) as acquired:
            if acquired:
                out = handler(project, payload, data_dir)
            else:
                out = None
                _log_note(data_dir, f"{event}: session lock not acquired within "
                                    f"{LOCK_TIMEOUT_S:g} s; hook skipped")
        if out:
            print(json.dumps(out))
    except Exception:  # noqa: BLE001 — a hook must never fail loudly
        _log(data_dir or env.get("CLAUDE_PLUGIN_DATA"), event)
    return 0


def _has_config(start):
    """True when `.claimlock.toml` exists in `start` or one of its ancestors —
    the same places `project.load` looks. A bare claims directory is not a
    store for hooks, and a config below `start` is not seen."""
    try:
        start = start.resolve()
    except OSError:
        return False
    return any((d / P.CONFIG).is_file() for d in (start, *start.parents))


def _parse_payload(stdin_text):
    """(payload dict, malformed bool). Empty/absent stdin and a syntactically
    valid but non-dict JSON value both degrade to `{}` silently and are not
    "malformed" — only text that fails to parse as JSON at all is, which
    main() logs a note about once it knows there's a store (Finding D)."""
    if not stdin_text or not stdin_text.strip():
        return {}, False
    try:
        payload = json.loads(stdin_text)
    except ValueError:
        return {}, True
    return (payload if isinstance(payload, dict) else {}), False


@contextlib.contextmanager
def _session_lock(data_dir, sid):
    """Exclusive per-session lock serialising one session's load->check->save
    across concurrent hook processes. Yields True once held, or False if it
    could not be acquired within a bounded wait — the caller then skips
    running the handler entirely rather than risk two processes reading,
    computing and writing the same session-state file at once. Never blocks
    indefinitely and never raises. Without `fcntl` (non-POSIX, e.g. Windows)
    coordination is not possible; proceed unlocked (best effort) rather than
    fail the hook — this is a known gap on that platform, not silent data
    loss, since state writes still go through a unique temp file.
    """
    if fcntl is None:
        yield True
        return
    lock_path = data_dir / "sessions" / f"{sid}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")
    acquired = False
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(LOCK_POLL_S)
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def _log_dir(where):
    """Where hook-errors.log goes: the plugin data dir when known, else a
    per-user ~/.claimlock (never a shared temp path other users can write)."""
    return Path(where) if where else Path.home() / ".claimlock"


def _log(where, event):
    try:
        d = _log_dir(where)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hook-errors.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()} {event}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def _log_note(where, text):
    """Append a plain one-line note (no traceback) — for a condition worth
    surfacing to an operator that is not itself a caught exception."""
    try:
        d = _log_dir(where)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hook-errors.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()} {text}\n")
    except OSError:
        pass


def _sid(payload):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "no-session"))


def _state_path(data_dir, payload):
    return data_dir / "sessions" / f"{_sid(payload)}.json"


def _load_state(path, project):
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) and st.get("root") == str(project.root) else None


def _save_state(path, st):
    """Write via a unique temp file in the same directory, then atomically
    replace: a fixed name (e.g. "<sid>.tmp") let two concurrent writers for
    the same session collide — one's `os.replace` could find the other had
    already renamed the shared tmp path away, raising FileNotFoundError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(st))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def survey(project):
    hasher = C.open_hasher(project)
    results = C.evaluate(project, hasher)
    hasher.save()
    ids = {r.claim.id for r in results}
    markers, _ = refs.scan(project)
    s = {"invalid": sorted(r.claim.id for r in results if r.problems and not r.claim.conflicted),
         "conflicted": sorted(r.claim.id for r in results if r.claim.conflicted)}
    for k in C.NON_FRESH:
        s[k] = sorted(r.claim.id for r in results if r.state == k)
    s["owed"] = sorted(r.claim.id for r in results if r.claim.status == "owed")
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


def _init_head(project, st, head=_UNSET):
    """Reset the HEAD-change probe: mark_paths + their mtimes + last_head.

    `head`, when given, is a HEAD value the caller already read this same
    tick — head_check's primary branch passes its own `gitio.head` result so
    this doesn't read HEAD a second time, closing the race/failure window
    that opened between two separate reads. A `None` result (a transient git
    failure, or `head` explicitly passed as None) never erases an
    already-known `last_head`: losing it would make the next check diff only
    the newest commit instead of the whole range of commits actually missed.
    """
    paths = gitio.head_mark_paths(project.root)
    st["mark_paths"] = paths
    if not paths:
        st["last_head"] = None
    else:
        current = gitio.head(project.root) if head is _UNSET else head
        if current is not None or "last_head" not in st:
            st["last_head"] = current
    st["marks"] = _stat_marks(paths)
    st["probed_at"] = time.time()
    if paths and "email" not in st:
        # Cached for the session: who "owed to you" means. Read only when
        # HEAD tracking is (re)initialised, never on the stat-only path.
        st["email"] = gitio.user_email(project.root)


def _claim_rel(project, claim):
    try:
        return claim.path.relative_to(project.root).as_posix()
    except ValueError:
        return None


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
    old = st.get("last_head")
    new = gitio.head(project.root)
    # Pass `new` in: the checked-out branch (and so the ref file) may have
    # changed too, but re-reading here would be a second `gitio.head` call
    # for this same check — see _init_head's docstring for why that matters.
    _init_head(project, st, head=new)
    if new is None or new == old:
        return None
    changed = set(gitio.changed_paths(project.root, old, new))
    hasher = C.open_hasher(project)
    results = C.evaluate(project, hasher)
    hasher.save()
    # One `git log` over the range attributes every changed path to the
    # newest commit that touched it.
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


class _HeadReport(str):
    """The HEAD-moved message, carrying which claim ids and `path:id` markers
    it actually printed, so Stop does not name them a second time."""
    named = frozenset()


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


def session_start(project, payload, data_dir):
    s, results = survey(project)
    st = {"root": str(project.root), "baseline": s}
    _init_head(project, st)
    _save_state(_state_path(data_dir, payload), st)
    me = st.get("email")
    mine = sorted(r.claim.id for r in results if me and r.claim.status == "owed" and r.claim.owed_by == me)
    lead = (f"claimlock: owed to you: {len(mine)} ({', '.join(mine[:10])}{'…' if len(mine) > 10 else ''}).\n"
            if mine else "")
    n = len(results)
    if not any(s.values()):
        text = f"claimlock: {n} claims, all fresh. {RECHECK}"
    else:
        counts = ", ".join(f"{len(v)} {k}" for k, v in s.items())
        areas = Counter(r.claim.area for r in results if r.problems or r.state in C.NON_FRESH)
        area_line = ("Affected areas: " + ", ".join(f"{a} ({c})" for a, c in areas.most_common(8))
                     + (", …" if len(areas) > 8 else "") + ".\n") if areas else ""
        text = f"claimlock: {n} claims — {counts}.\n{area_line}{RECHECK}"
    text = lead + text
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text[:LIMIT]}}


def stop(project, payload, data_dir):
    path = _state_path(data_dir, payload)
    st = _load_state(path, project)
    if st is None:
        _save_state(path, _new_state(project))
        return None
    s, results = survey(project)
    base = st.get("baseline") or {}
    new = {k: [x for x in v if x not in set(base.get(k, []))] for k, v in s.items()}
    head_msg = head_check(project, st)
    st["baseline"] = s
    _save_state(path, st)
    # "Since the last check", not "this session": drift that arrived by
    # `git pull` is new to this baseline too. Anything the HEAD-moved report
    # below already names is not repeated here.
    named = getattr(head_msg, "named", frozenset())
    new = {k: [x for x in v if x not in named] for k, v in new.items()}
    # Drift whose cited source you have edited but not committed is yours;
    # the rest arrived some other way (a pull, a tool, another process).
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
    return {"systemMessage": "\n".join(parts)[:LIMIT]} if parts else None


def _since_phrase(kind, items):
    shown = ", ".join(items[:5]) + ("…" if len(items) > 5 else "")
    n = len(items)
    if kind == "dangling":
        return f"{n} dangling marker{'' if n == 1 else 's'} appeared ({shown})"
    return f"{n} claim{'' if n == 1 else 's'} became {kind} ({shown})"


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
