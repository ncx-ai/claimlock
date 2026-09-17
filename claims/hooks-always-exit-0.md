---
id: hooks-always-exit-0
area: hooks
status: verified
evidence:
  - kind: test
    ref: tests/test_hooks.py::Contract::test_internal_errors_are_logged_not_shown
  - kind: test
    ref: tests/test_hooks.py::Contract::test_malformed_stdin_with_a_store_logs_a_note_but_still_reports_normally
sources:
  - path: lib/claimlock/hooks.py
    blob: 5280687f2a833a2b81e5816e187aaf9e6c4fb427
pins: 6b88aad851d14320d5b502ee79c8478fd1a73859
---
Every claimlock hook (`session-start`, `stop`, `post-tool-use`, `post-edit`)
exits 0 no matter what happens inside it, and never sets `decision`.

Enforcement site: `hooks.main` wraps the whole dispatch (payload parsing,
config/project load, handler call, session-lock, logging) in one
`try/except Exception`, and the final `return 0` sits *outside* that block,
so nothing inside it — a bad `.claimlock.toml`, an unknown hook event, stdin
that fails to parse as JSON, an unhandled exception anywhere in a handler —
can produce a different exit code; the only thing an internal error does is
append a line to `hook-errors.log`. `test_internal_errors_are_logged_not_shown`
drives this with an invalid config (`unknown key`) and an unrecognised event
name; `test_malformed_stdin_with_a_store_logs_a_note_but_still_reports_normally`
drives it with stdin that isn't valid JSON at all. Both go through
`HookCase.hook()`, which itself asserts `rc == 0` on every call — so this
claim would be falsified the moment either test's underlying scenario ever
produced a non-zero exit or a `decision` key.
