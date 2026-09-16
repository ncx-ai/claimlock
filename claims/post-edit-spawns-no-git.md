---
id: post-edit-spawns-no-git
area: hooks
status: verified
evidence:
  - kind: test
    ref: tests/test_edit_notice.py::NoGit::test_post_edit_invokes_no_git_cited_or_not
sources:
  - path: lib/claimlock/hooks.py
    blob: aff056e8207d1693234db2007d3cc6cb45ea6a1c
pins: 748ac5aec0d83a16305b3b827416bcc041aac98f
---
The `post-edit` hook path (`PostToolUse` after Edit/Write/MultiEdit/
NotebookEdit) never spawns a git subprocess, whether or not the edited file
resolves to a cited claim.

Enforcement site: `hooks.post_edit`'s own docstring states the rule ("No
hashing and no git, ever") and its body backs it structurally — on a cold
session it builds `st = {"root": str(project.root)}` directly rather than
calling `_new_state` (which runs `survey()` and opens a git-aware hasher),
precisely so the very first call in a session spawns no git either.
`test_post_edit_invokes_no_git_cited_or_not` puts a fake `git` executable
first on `PATH` that logs every invocation and exits 1, then drives
`post-edit` twice in the same git-backed repo — once with a path that is
cited by a claim, once with one that isn't — and asserts the fake was never
called and the hook still exits 0. This claim is falsified the moment
`post_edit` (or anything it calls) shells out to git.
