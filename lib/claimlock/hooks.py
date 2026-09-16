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
import hashlib
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
SUBJECT_CAP = 60
EMAIL_CAP = 80
PROBE_EVERY_S = 30
LOCK_TIMEOUT_S = 5.0
LOCK_POLL_S = 0.05
SESSION_IDLE_S = 7 * 24 * 3600
LOG_MAX_BYTES = 1 << 20
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
        if event == "session-start":
            # Once per session, not on every tool call.
            _prune_sessions(data_dir, _sid(payload))
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
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    f, acquired = None, False
    try:
        while True:
            if f is None:
                f = open(lock_path, "a+")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(LOCK_POLL_S)
                continue
            # `_prune_sessions` may have removed this file while we waited. A
            # lock on a removed file excludes nobody — the next process creates
            # a new file at the path — so take the lock again on that one.
            try:
                held, now = os.fstat(f.fileno()), os.stat(lock_path)
                same = (held.st_dev, held.st_ino) == (now.st_dev, now.st_ino)
            except OSError:
                same = False
            if same:
                acquired = True
                try:
                    os.utime(lock_path)  # the session's activity, for `_prune_sessions`
                except OSError:
                    pass
                break
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()
            f = None
            if time.monotonic() >= deadline:
                break
        yield acquired
    finally:
        if f is not None:
            if acquired:
                try:
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    pass
            f.close()


def _prune_sessions(data_dir, keep):
    """Remove the files of sessions idle longer than SESSION_IDLE_S.

    Every hook run leaves `<sid>.lock` and `<sid>.json` (a crash can also leave
    a `<sid>.json.*.tmp`), one set per session, which would otherwise pile up
    forever. A session's idle time is measured from its newest file — the lock
    is touched each time it is taken and the state file on each save. Its
    files are removed only while this process holds its lock, after checking
    again that none has changed, so a session resuming at that moment is never
    interleaved (`_session_lock` takes the lock again on a file removed under
    it). Without `fcntl` (Windows) there is no lock and the check is best
    effort. The current session is kept.
    Best effort: any error leaves the files for a later run."""
    d = data_dir / "sessions"
    try:
        entries = list(os.scandir(d))
    except OSError:
        return
    groups = {}
    for e in entries:
        if not e.name.endswith((".json", ".lock", ".tmp")):
            continue
        sid = e.name.split(".", 1)[0]
        if sid == keep:
            continue
        try:
            mtime = e.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        newest, paths = groups.get(sid, (0.0, []))
        groups[sid] = (max(newest, mtime), paths + [Path(e.path)])
    cutoff = time.time() - SESSION_IDLE_S
    for sid, (newest, paths) in groups.items():
        if newest >= cutoff:
            continue
        lock = d / f"{sid}.lock"
        f = None
        try:
            if fcntl is not None and lock.exists():
                f = open(lock, "a+")
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # OSError: in use, keep it
                # The session may have run and saved between the scan and the
                # lock; its files are then no longer idle.
                if any(p.stat().st_mtime >= cutoff for p in paths if p.exists()):
                    continue
            for p in sorted(paths, key=lambda p: p == lock):  # the lock last
                p.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            if f is not None:
                f.close()  # closing releases the lock


def _log_dir(where):
    """Where hook-errors.log goes: the plugin data dir when known, else a
    per-user ~/.claimlock (never a shared temp path other users can write)."""
    return Path(where) if where else Path.home() / ".claimlock"


def _append_log(where, text):
    """Append to hook-errors.log, first moving a log over LOG_MAX_BYTES to
    hook-errors.log.1 (replacing an older one), so it never grows unbounded."""
    try:
        d = _log_dir(where)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "hook-errors.log"
        try:
            if path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, d / "hook-errors.log.1")
        except FileNotFoundError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def _log(where, event):
    _append_log(where, f"{datetime.now().astimezone().isoformat()} {event}\n{traceback.format_exc()}\n")


def _log_note(where, text):
    """Append a plain one-line note (no traceback) — for a condition worth
    surfacing to an operator that is not itself a caught exception."""
    _append_log(where, f"{datetime.now().astimezone().isoformat()} {text}\n")


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
    me = C.normalize_email(st.get("email"))
    owed_new = sorted(r.claim.id for r in results
                      if me and r.claim.status == "owed" and C.normalize_email(r.claim.owed_by) == me
                      and _claim_rel(project, r.claim) in changed)
    conflicted = sorted(r.claim.id for r in results if r.claim.conflicted)
    ids = {r.claim.id for r in results}
    markers, _ = refs.scan(project, only=changed)
    dangling = sorted({f"{m.path}:{m.id}" for m in markers if m.id not in ids})
    if not (hit or dangling or owed_new or conflicted):
        return None
    return _head_report(old, new, hit, dangling, owed_new, conflicted)


class _HeadReport(str):
    """The HEAD-moved message, already cut to LIMIT, carrying the `(kind, id)`
    pairs that survived that cut — kind is the survey bucket ("stale",
    "owed", "conflicted", "dangling", …) and id a claim id or `path:id`
    marker — so Stop does not repeat them in that same bucket, and never
    suppresses one nobody saw, nor one shown only under another kind."""
    named = frozenset()


def _cap(text, n):
    return text if len(text) <= n else text[:n] + "…"


def _head_report(old, new, hit, dangling, owed_new=(), conflicted=()):
    # Ordered by what a reader must not miss: hand-offs and conflicts come
    # before the attribution list, whose subjects make it the long part.
    pieces = []  # (text, the (kind, id) it names, or None)

    def add(text, name=None):
        pieces.append((text, name))

    def names(items, limit, kind):
        for i, x in enumerate(items[:limit]):
            if i:
                add(", ")
            add(x, (kind, x))
        if len(items) > limit:
            add("…")

    add(f"claimlock: HEAD moved {(old or 'none')[:7]}→{new[:7]} (a commit, merge, rebase, pull or checkout).")
    if owed_new:
        add(" Now owed to you: ")
        names(owed_new, 10, "owed")
        add(".")
    if conflicted:
        add(f" {len(conflicted)} claim file(s) have merge conflicts (")
        names(conflicted, 5, "conflicted")
        add(") — run `claimlock resolve`.")
    if hit:
        add(f" Files changed in that range back {len(hit)} claim(s) that are no longer fresh: ")
        for i, (cid, state, src, w) in enumerate(hit[:10]):
            if i:
                add("; ")
            add(cid, (state, cid))
            if w:
                email = _cap(w[1], EMAIL_CAP) or "unknown"
                add(f' ({state}): {src} changed by {email} in {w[0]} "{_cap(w[2], SUBJECT_CAP)}"')
            else:
                add(f" ({state})")
        add(("…" if len(hit) > 10 else "") + ".")
    if dangling:
        add(" Markers naming no claim: ")
        names(dangling, 10, "dangling")
        add(".")
    add(" Re-check each with `claimlock diff <id>`; `claimlock verify <id>` only after re-checking.")
    named, end = set(), 0
    for text, name in pieces:
        end += len(text)
        if name is not None and end <= LIMIT:
            named.add(name)
    report = _HeadReport("".join(text for text, _ in pieces)[:LIMIT])
    report.named = frozenset(named)
    return report


def session_start(project, payload, data_dir):
    path = _state_path(data_dir, payload)
    prev = _load_state(path, project)  # None unless the same root's state survived (e.g. a `compact`)
    s, results = survey(project)
    st = {"root": str(project.root), "baseline": s}
    if prev is not None:
        # Carry the post-edit notice's own state forward across a mid-session
        # SessionStart (its matcher includes "compact"): otherwise every
        # already-announced file gets announced again after a compaction.
        # Nothing else survives — baseline and the HEAD marks are always
        # re-established fresh, as before.
        for k in ("edited_notified", "cited", "cited_signature"):
            if k in prev:
                st[k] = prev[k]
    _init_head(project, st)
    _save_state(path, st)
    me = C.normalize_email(st.get("email"))
    mine = sorted(r.claim.id for r in results
                  if me and r.claim.status == "owed" and C.normalize_email(r.claim.owed_by) == me)
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
    # A kind the baseline does not carry (state written before that kind
    # existed) has no known "before": report none of it this once; the
    # baseline gains the key below.
    new = {k: ([x for x in v if x not in set(base[k])] if k in base else []) for k, v in s.items()}
    head_msg = head_check(project, st)
    # "Since the last check", not "this session": drift that arrived by
    # `git pull` is new to this baseline too. Anything the HEAD-moved report
    # already names under the same kind is not repeated here.
    named = getattr(head_msg, "named", frozenset())
    new = {k: [x for x in v if (k, x) not in named] for k, v in new.items()}
    # Drift whose cited source you have edited but not committed is yours;
    # the rest arrived some other way (a pull, a tool, another process).
    # Mid-merge/rebase/cherry-pick, the working tree differs from HEAD because
    # of git, so none of it is attributed to the person's edits.
    dirty = (set(gitio.dirty_paths(project.root))
             if st.get("mark_paths") and not gitio.operation_in_progress(project.root) else set())
    by_id = {r.claim.id: r for r in results}
    yours, others = {}, {}
    for kind, items in new.items():
        for item in items:
            r = by_id.get(item)
            mine = r is not None and dirty and any(sp.path in dirty for sp in r.claim.sources)
            (yours if mine else others).setdefault(kind, []).append(item)
    # The HEAD report goes first: it is already within LIMIT, so everything its
    # `named` suppressed from the lines below is visible in the final message.
    pieces = [(str(head_msg), None)] if head_msg else []
    for lead, groups, tail in (
            ("claimlock: from your uncommitted edits, ", yours, ". Inspect with `claimlock diff <id>`."),
            ("claimlock: since the last check, ", others,
             ". Inspect with `claimlock diff <id>` or `claimlock refs`.")):
        if not groups:
            continue
        if pieces:
            pieces.append(("\n", None))
        pieces.append((lead, None))
        for i, (kind, items) in enumerate(groups.items()):
            if i:
                pieces.append(("; ", None))
            pieces.extend(_since_pieces(kind, items))
        pieces.append((tail, None))
    shown, end = set(), 0
    for text, key in pieces:
        end += len(text)
        if key is not None and end <= LIMIT:
            shown.add(key)
    # Carry forward what the message did not show — cut by LIMIT, or only
    # counted past a phrase's first five — by keeping it out of the baseline,
    # so the next Stop reports it again. Built before the state is saved.
    unshown = {(k, x) for groups in (yours, others) for k, items in groups.items() for x in items} - shown
    st["baseline"] = {k: [x for x in v if (k, x) not in unshown] for k, v in s.items()}
    _save_state(path, st)
    text = "".join(text for text, _ in pieces)[:LIMIT]
    return {"systemMessage": text} if text else None


def _since_pieces(kind, items):
    """One "N claims became <kind> (a, b, …)" phrase as (text, (kind, id) | None)
    pieces: the first five ids are listed, the rest only counted."""
    n = len(items)
    if kind == "dangling":
        head = f"{n} dangling marker{'' if n == 1 else 's'} appeared ("
    else:
        head = f"{n} claim{'' if n == 1 else 's'} became {kind} ("
    out = [(head, None)]
    for i, x in enumerate(items[:5]):
        if i:
            out.append((", ", None))
        out.append((x, (kind, x)))
    tail = " — run `claimlock resolve`" if kind == "conflicted" else ""
    out.append((("…" if n > 5 else "") + ")" + tail, None))
    return out


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


CITED_STATUSES = ("verified", "owed")
EDIT_CLAIM_CAP = 5
EDIT_PATH_CAP = 3


def _edited_paths(project, payload):
    """Candidate edited paths from a PostToolUse payload, root-relative,
    deduped in the order they first appeared. Non-string values are ignored
    (spec §3.1). A path resolving outside the project root, or inside the
    claims directory (editing a claim is not source drift), is dropped.

    Collects `tool_input.file_path` (Edit/Write), `tool_input.notebook_path`
    (NotebookEdit, shape unconfirmed), and any `file_path` inside a list at
    `tool_input.edits` (MultiEdit, shape unconfirmed) — reading defensively
    per spec §2: an unrecognised shape yields no candidates, never an error.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    raw = []
    for key in ("file_path", "notebook_path"):
        v = tool_input.get(key)
        if isinstance(v, str):
            raw.append(v)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict) and isinstance(e.get("file_path"), str):
                raw.append(e["file_path"])
    root = project.root
    out, seen = [], set()
    for r in raw:
        p = Path(r)
        if not p.is_absolute():
            p = root / p
        try:
            p = p.resolve()
        except OSError:
            continue
        if not P.is_within(p, root) or P.is_within(p, project.claims_dir):
            continue
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


def _claims_signature(project):
    """Digest of (name, size, mtime_ns) for every `*.md` in the claims
    directory. Stats each file rather than the directory alone: editing a
    claim's content in place changes neither the directory's mtime nor its
    entry count, only the file's own size/mtime_ns (spec §3.2)."""
    try:
        names = sorted(os.listdir(project.claims_dir))
    except OSError:
        return ""
    parts = []
    for name in names:
        if not name.endswith(".md"):
            continue
        try:
            st = os.stat(project.claims_dir / name)
        except OSError:
            continue
        parts.append(f"{name}\t{st.st_size}\t{st.st_mtime_ns}")
    data = "\n".join(parts)
    return hashlib.sha1(data.encode("utf-8", "surrogateescape")).hexdigest()


def _cited_index(project, st):
    """{source path: [claim id, ...]} for verified/owed claims only (spec
    §3.2) — those are the only statuses carrying a pin an edit can drift.
    Cached in `st` under "cited", invalidated by "cited_signature"
    (`_claims_signature`); a hit costs one `os.listdir` plus one `os.stat`
    per claim file and no claim-file reads. `st["cited_status"]` (claim id ->
    status) rides along in the same cache entry so callers can label a hit
    without a second pass over the claims."""
    sig = _claims_signature(project)
    cached = st.get("cited")
    if st.get("cited_signature") == sig and isinstance(cached, dict):
        return cached
    index, status_by_id = {}, {}
    try:
        claims = C.load_claims(project)
    except C.StoreMissing:
        claims = []
    for claim in claims:
        if claim.status not in CITED_STATUSES:
            continue
        status_by_id[claim.id] = claim.status
        for s in claim.sources:
            ids = index.setdefault(s.path, [])
            if claim.id not in ids:
                ids.append(claim.id)
    st["cited"] = index
    st["cited_signature"] = sig
    st["cited_status"] = status_by_id
    return index


def _edit_notice(hits, status_by_id):
    """One `claimlock: <path> backs N claim(s) — id (status), …` message per
    hit path (spec §3.3): at most EDIT_CLAIM_CAP claims per path, then `…`;
    at most EDIT_PATH_CAP paths, then `…`. Callers cut the result to LIMIT.
    The closing pronoun agrees with the TOTAL claim count across every hit
    (not just the ones actually named under the caps) — "it" only when
    exactly one claim, anywhere, is at stake; "them" otherwise."""
    total = sum(len(ids) for _, ids in hits)
    parts = []
    for path, ids in hits[:EDIT_PATH_CAP]:
        shown = ids[:EDIT_CLAIM_CAP]
        names = ", ".join(f"{cid} ({status_by_id.get(cid, 'unverified')})" for cid in shown)
        if len(ids) > EDIT_CLAIM_CAP:
            names += ", …"
        n = len(ids)
        parts.append(f"{path} backs {n} claim{'' if n == 1 else 's'} — {names}")
    if len(hits) > EDIT_PATH_CAP:
        parts.append("…")
    pronoun = "it" if total == 1 else "them"
    return (f"claimlock: {'; '.join(parts)}. Your edit may have invalidated {pronoun}: "
            "re-check with `claimlock diff <id>` before any `claimlock verify`.")


def post_edit(project, payload, data_dir):
    """Name the verified/owed claims a just-edited file backs — a notice at
    the moment of the edit rather than after the fact (spec §3). No hashing
    and no git, ever: the edit just happened, so a hit is presumed drifted,
    and computing freshness would add cost to answer a question this notice
    does not ask. Silent (returns None) whenever nothing was edited, nothing
    resolves to a usable in-root non-claims path, everything resolved was
    already notified this session, or nothing cited resolves."""
    paths = _edited_paths(project, payload)
    if not paths:
        return None
    path = _state_path(data_dir, payload)
    st = _load_state(path, project)
    if st is None:
        # Deliberately not `_new_state`: that runs `survey()`, which opens a
        # git-aware hasher — this event must spawn no git, ever, even on its
        # very first call in a session. A later session-start/stop/
        # post-tool-use call still establishes the full baseline normally.
        st = {"root": str(project.root)}
    index = _cited_index(project, st)
    notified = st.get("edited_notified")
    notified = list(notified) if isinstance(notified, list) else []
    notified_set = set(notified)
    hits = [(p, index[p]) for p in paths if p not in notified_set and index.get(p)]
    if not hits:
        _save_state(path, st)  # persist any cache rebuild even when silent
        return None
    st["edited_notified"] = notified + [p for p, _ in hits]
    _save_state(path, st)
    status_by_id = st.get("cited_status") or {}
    text = _edit_notice(hits, status_by_id)[:LIMIT]
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


HANDLERS = {"session-start": session_start, "stop": stop, "post-tool-use": post_tool_use,
            "post-edit": post_edit}
