---
id: check-exits-2-when-store-unreadable
area: cli
status: verified
evidence:
  - kind: test
    ref: tests/test_cli_read.py::UnreadableClaimsDir::test_unreadable_claims_dir_is_exit_2_not_a_clean_store
  - kind: test
    ref: tests/test_cli_read.py::Unreadable::test_missing_claims_dir_is_exit_2
sources:
  - path: lib/claimlock/cli.py
    blob: 7824085811af5f8fd932d31ecb792482dce1753b
  - path: lib/claimlock/claims.py
    blob: 7c7f8293305418eb7b9cbd7ade8145ccee275c3e
pins: 250aa2cf8a421f2f7e8b9a18a539fc4d05c7c35d
---
`claimlock check` exits 2 — never 0, never 1 — when the claims directory
cannot be read, whether because it does not exist or because it exists but
cannot be listed (permission denied).

Enforcement sites: `claims.load_claims` raises `StoreMissing` when the
directory is absent and `StoreUnreadable` (a `StoreMissing` subclass, "so
every caller that maps 'no readable store' to exit 2 handles it without
change") when `os.listdir` on it raises; `cli.main`'s dispatcher catches
`(P.ConfigError, C.StoreMissing)` and returns 2, printing the message to
stderr — deliberately never falling through to `check`'s normal "0 claims"
report, because reading an unreadable directory as an empty, clean store
would be a false clean.
`test_unreadable_claims_dir_is_exit_2_not_a_clean_store` chmods `claims/` to
`0` and asserts `rc == 2` with `"cannot be read"` in stderr and no "0 claims"
in stdout; `test_missing_claims_dir_is_exit_2` removes the directory
entirely and asserts the same exit code. This claim is falsified by any
change that makes an unreadable or absent claims directory report a `0` or
`1` exit instead.
