---
id: evidence-ask-runners-passes-no-ref-derived-string-to-a-subprocess
area: evidence
status: verified
evidence:
  - kind: test
    ref: tests/test_runners.py::Consulting::test_the_report_path_handed_to_json_is_never_a_path_from_the_ref
sources:
  - path: lib/claimlock/runners.py
    blob: e3290cef08f8bb067e898a54844f3fd8fb717d6c
  - path: lib/claimlock/evidence.py
    blob: adcd998137f0ed2d27683943fa6ceb3fbc0646bd
pins: 75a985490f4e6e59f1c661f02280db958fc448d2
---
`claimlock evidence --ask-runners` never passes a string derived from a
claim's `ref` to a command line — every `subprocess.run` argv is a fixed
list, and the two things a ref could otherwise contribute (a file path, a
test name) never reach one.

Enforcement: `evidence.py::audit`'s explicit-ref loop runs an EXPLICIT ref's
path through `project.safe_source` before it is used for anything, and the
only two subprocess call sites, `runners.py::vitest_tests` and `cargo_tests`,
build their `argv` as a literal list — `["npx", "vitest", "list", "--json",
str(out)]` and `["cargo", "test", "--all-targets", "--", "--list"]` — with no
ref-derived string ever interpolated into either. A ref's test NAME is never
passed to a command at all: it is only ever compared, in Python, against
what the runner printed (`_runner_lists`). The one path in the whole feature
that looks like a hazard, `--json <path>`, is the report path `vitest_tests`
hands the subprocess — and that path is always one claimlock creates itself
inside a fresh `tempfile.TemporaryDirectory()`, never a path from a ref, a
claim, or the repository, because that flag *writes* its report there rather
than reading a target (during this feature's own investigation, an earlier
version pointed it at a ref's path and truncated a real 251-line test file).

Falsified by `tests/test_runners.py::Consulting::
test_the_report_path_handed_to_json_is_never_a_path_from_the_ref`, which
intercepts `subprocess.run`, asserts the report path is absolute, outside the
project root, and does not contain the cited file's name, and asserts the
argv is exactly `["npx", "vitest", "list", "--json", <report>]` with no sixth
element — i.e. no ref-derived argument anywhere in it.
