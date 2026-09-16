---
id: search-exits-1-only-on-no-match
area: cli
status: verified
evidence:
  - kind: test
    ref: tests/test_output_budget.py::SearchDefaultShape::test_no_match_message_and_exit_code_unchanged
  - kind: test
    ref: tests/test_output_budget.py::SearchQueryWithBraceIsNeverAFormatString::test_named_field_key_does_not_crash
  - kind: test
    ref: tests/test_output_budget.py::SearchQueryWithBraceIsNeverAFormatString::test_empty_braces_do_not_crash
sources:
  - path: lib/claimlock/cli.py
    blob: 883fb0e1c2150fdd47a8b82680bfaf76cca63b06
pins: 54d71e8cbd22e3b0df0ea4d3e72cdfcc58d74bed
---
`claimlock search` exits 1 exactly when the query has zero hits, in both
ranked (default) and `--literal` mode — never for a query that found results,
however that query is spelled.

Enforcement sites: `_search_ranked` and `_search_literal` each hold a single
`if not hits: ... return 1` (`_search_literal` counts substring matches
directly; `_search_ranked` checks `rank.search(index, args.query)`), with
`return 0` on every other path. On 2026-09-16 this held only when the query
avoided `{`/`}`: the cut-note builder used to construct `more` as an
f-string carrying the raw, arbitrary query text and hand it to `_capped`,
which calls `.format(n=...)` on it — so a query like `widget {widget}` raised
an uncaught `KeyError`/`IndexError` mid-command and the process exited 1,
indistinguishable from "no match" even though hits had already been found
and printed. The fix moved the cut-note interpolation to run after
`_capped`, never re-scanning the query for `{...}`.
`test_no_match_message_and_exit_code_unchanged` is the positive case (a real
no-match query still exits 1 with the documented message);
`test_named_field_key_does_not_crash` and `test_empty_braces_do_not_crash`
are the regression — 15 real hits, a query containing `{widget}` or `{}`,
and an assertion of `rc == 0` with all hits printed. This claim is falsified
by any query that finds results yet exits 1.
