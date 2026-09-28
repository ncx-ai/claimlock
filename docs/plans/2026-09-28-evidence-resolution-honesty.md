# Evidence resolution honesty — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `claimlock evidence` stop reporting a ref as `resolved` when nothing about that ref was checked, by refusing a locator that cannot discriminate, accepting an explicit `<file>::<test>` ref shape, and naming a static match apart from a runner-confirmed one.

**Architecture:** `evidence.py` is a pure module: it turns a ref into something to look for, and audits a claim set against ONE walk of the repository that `refs.files` already performs. Every change here stays inside that shape — the ambiguity rule reuses the same walk by counting files per locator instead of stopping at the first hit; the explicit ref shape is parsed before the locator rule is reached; and the only new I/O (`--ask-runners`) lives behind an opt-in flag and a fixed argv per language, never touching a claim's bytes.

**Tech Stack:** Python ≥ 3.11 standard library, `unittest`.

**Spec:** `docs/specs/2026-09-28-evidence-resolution-honesty-design.md` — read all of it, especially §2 (why the current rule was correct when written, so it is not described as a blunder), §4.1 (the threshold and why 3), §4.3 (four states, and why `matched` must not print as `resolved`) and §4.4 (untrusted input: nothing from a ref reaches a shell).

**Base:** `da16035`.

## Global Constraints

- Python ≥ 3.11 standard library only. **No new dependency, no network call, no persisted index, no cache file.**
- Tests: `python3 -m unittest discover -s tests` from the repo root — **496** at the start of this plan, and **`FORCE_COLOR` must be unset or empty when running them**. A forced-colour environment makes Python 3.14's argparse colour `--help`, and `tests/test_skills_format.py::test_every_command_a_skill_names_exists` parses that help as plain text, so it fails for a reason that has nothing to do with the code. Measured 2026-09-28: `FORCE_COLOR=3` in the shell → 1 failure; `FORCE_COLOR=` → OK, 496 tests.
- `python3 bin/claimlock self-test` stays at **19/19**.
- **Only `kind: test` refs are ever resolved.** `measurement`, `source` and `run` refs are prose by design (2026-09-16 spec §2) — never parsed, never reported.
- `check` must not get slower and must gain no scan: its baseline is 0.096 s and one evidence scan costs 0.88 s. No scan may be added to `check`, `_evaluate`, or any hook.
- **`unresolved` is the only failing state.** `unlocatable` and `matched` never change an exit code — a store adopting this must not meet a cliff, the same reasoning that keeps orphans non-failing.
- **A claim file is untrusted input.** Nothing from a `ref` is passed to a shell, interpolated into a command, or used as a path outside the repository. `--ask-runners` runs a fixed argv per language, discovered from the repository's own manifests.
- The README must list every command and flag in `claimlock --help` (`tests/test_plugin_manifest.py`); skills obey `tests/test_skills_format.py`, whose `FORBIDDEN` regex bars project-specific words.
- Every new listing is bounded like its neighbours (`LISTED_CLAIMS = 20`, `--full` to see all) per the output-budget spec.
- Revert temporary mutations by re-applying the inverse edit, never `git checkout --`. Stage explicit paths, never `git add -A`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018KqbgYbCYUCrWdS3PmQqWt
  ```

## File map

| File | Change |
|---|---|
| `lib/claimlock/evidence.py` | Task 1: `AMBIGUOUS_FILES`, per-locator file counts, `ambiguous` reason on `Check`. Task 2: `parse_ref`, exact file+name resolution. Task 3: `matched` outcome. Task 4: `ask_runners` hook point |
| `lib/claimlock/runners.py` | NEW (Task 4): `vitest_tests`, `cargo_tests` — each returns `{(rel_file, test_name)}`; the only subprocess in the feature |
| `lib/claimlock/cli.py` | Task 1: census counts ambiguity. Task 3: `MATCHED` listing + census. Task 4: `--ask-runners` flag |
| `tests/test_evidence.py` | Tasks 1–3: cases |
| `tests/test_runners.py` | NEW (Task 4) |
| `README.md` | Task 3: the four states table. Task 4: `--ask-runners` |

---

### Task 1: A locator that cannot discriminate is `unlocatable`, not `resolved`

The ship-alone task. After it, `evidence` can no longer report a ref as checked
because a common English word appears somewhere in the repository.

**Files:**
- Modify: `lib/claimlock/evidence.py`
- Modify: `lib/claimlock/cli.py` (`cmd_evidence` census only)
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `evidence.AMBIGUOUS_FILES = 3`; `Check` gains a fifth field
  `reason: str | None` (`"ambiguous"`, `"no-locator"`, or `None`);
  `audit(project, claims, globs=None)` keeps its
  `([Check], scanned, skipped)` return shape.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evidence.py`. The existing file builds a project fixture;
reuse whatever helper it already uses for that (read the top of the file first)
rather than inventing a second one.

```python
    def test_a_locator_in_many_files_is_unlocatable_not_resolved(self):
        # Four files all containing the token, one claim citing it. The token is
        # real and present; what it cannot do is identify a test.
        proj = self.project_with_files({
            f"src/f{i}.py": "def attributes_of_a_thing(): pass\n" for i in range(4)
        })
        claims = [self.claim("c1", evidence=[{"kind": "test", "ref": "'attributes a frame to its pane'"}])]
        checks, _scanned, _skipped = E.audit(proj, claims)
        self.assertEqual([c.outcome for c in checks], ["unlocatable"])
        self.assertEqual(checks[0].reason, "ambiguous")

    def test_a_locator_in_one_file_still_resolves(self):
        # The control. Without it the assertion above is satisfied by a rule
        # that calls everything unlocatable.
        proj = self.project_with_files({"src/a.py": "def flush_debits_conserves(): pass\n"})
        claims = [self.claim("c1", evidence=[{"kind": "test", "ref": "pkg::mod::flush_debits_conserves"}])]
        checks, _s, _k = E.audit(proj, claims)
        self.assertEqual([c.outcome for c in checks], ["resolved"])
        self.assertIsNone(checks[0].reason)

    def test_the_threshold_is_inclusive_at_its_boundary(self):
        # AMBIGUOUS_FILES files is still resolvable; one more is not. Pins the
        # boundary so a later refactor cannot move it silently by one.
        for n, expected in ((E.AMBIGUOUS_FILES, "resolved"), (E.AMBIGUOUS_FILES + 1, "unlocatable")):
            with self.subTest(files=n):
                proj = self.project_with_files({
                    f"src/g{i}.py": "def a_named_test_function(): pass\n" for i in range(n)
                })
                claims = [self.claim("c1", evidence=[{"kind": "test", "ref": "pkg::a_named_test_function"}])]
                checks, _s, _k = E.audit(proj, claims)
                self.assertEqual(checks[0].outcome, expected)

    def test_a_ref_with_no_locator_keeps_its_own_reason(self):
        # "no-locator" and "ambiguous" are both unlocatable and must stay
        # distinguishable: one is a ref claimlock cannot parse, the other a ref
        # it parsed and cannot use.
        proj = self.project_with_files({"src/a.py": "x = 1\n"})
        claims = [self.claim("c1", evidence=[{"kind": "test", "ref": "a b c"}])]
        checks, _s, _k = E.audit(proj, claims)
        self.assertEqual(checks[0].outcome, "unlocatable")
        self.assertEqual(checks[0].reason, "no-locator")
```

- [ ] **Step 2: Run them and watch them fail**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest tests.test_evidence -v
```

Expected: `AttributeError: module 'claimlock.evidence' has no attribute 'AMBIGUOUS_FILES'`, and
`TypeError` on `Check(...)` for the new field once the constant exists.

- [ ] **Step 3: Implement**

In `lib/claimlock/evidence.py`:

```python
# A locator that appears in more files than this identifies nothing. Measured on
# a 140-claim store: a genuine test name appears in 1 file (its definition) or 2
# (definition plus one citation); the lowest common-word locator measured is
# `attributes` at 79 files, and `platform` at 1,001. Nothing real sits between 3
# and 79, so this threshold separates two populations rather than splitting one.
AMBIGUOUS_FILES = 3
```

Give `Check` the field:

```python
@dataclass
class Check:
    claim_id: str
    ref: str
    locator: str | None
    outcome: str  # "resolved" | "unresolved" | "unlocatable"
    reason: str | None = None  # "ambiguous" | "no-locator" | None
```

In `audit`, replace the `seen` set with a count, and keep the walk single-pass:

```python
    counts = {}
    for p, _rel in R.files(project, None, globs):
        ...
        found = set()
        for tok in LOCATOR_RE.findall(text):
            if len(tok) >= MIN_LOCATOR and tok in needed:
                found.add(tok)
        for tok in found:                      # once per FILE, not per occurrence
            counts[tok] = counts.get(tok, 0) + 1
```

and decide from it:

```python
    for cid, ref, loc in wanted:
        reason = None
        if loc is None:
            outcome, reason = "unlocatable", "no-locator"
        else:
            n = counts.get(loc, 0)
            if n == 0:
                outcome = "unresolved"
            elif n > AMBIGUOUS_FILES:
                outcome, reason = "unlocatable", "ambiguous"
            else:
                outcome = "resolved"
        checks.append(Check(cid, ref, loc, outcome, reason))
```

- [ ] **Step 4: Run the new tests, then the whole suite**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest tests.test_evidence -v
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: the four new tests pass; the suite is OK at 500. If an existing
evidence test now fails, read it before changing it — a test asserting
`resolved` for a common-word locator is asserting the defect, and its
assertion moves to `unlocatable` with a comment saying why.

- [ ] **Step 5: Make the census say how many are ambiguous**

In `cmd_evidence`, the `UNLOCATABLE` line gains the reason and the count, so the
two kinds are distinguishable without `--full`:

```python
    _print_capped(unlocatable, cap, "… and {n} more unlocatable evidence refs — claimlock evidence --full",
                  lambda c: print(f"UNLOCATABLE {c.claim_id}  ({c.reason})  {c.ref}"))
    ambiguous = sum(1 for c in unlocatable if c.reason == "ambiguous")
    census = (f"claimlock: {len(resolved)} resolved, {len(unresolved)} unresolved, "
              f"{len(unlocatable)} unlocatable")
    if ambiguous:
        census += f" ({ambiguous} ambiguous)"
    census += f" in {scanned} files scanned"
```

- [ ] **Step 6: Prove the rule bites on the real store, and record the number**

```
cd ~/projects/boogy/.claude/worktrees/oauth-connections \
  && ~/.claude/plugins/cache/claimlock/claimlock/0.1.0/bin/claimlock evidence 2>&1 | tail -3
```

Expected before this task: `543 resolved, 6 unresolved, 0 unlocatable`.
Expected after: the resolved count DROPS and `unlocatable (N ambiguous)` appears,
with the vitest refs among them. Record both lines in the commit message — this
is the measurement that shows the change did something.

- [ ] **Step 7: Commit**

```bash
cd ~/projects/claimlock
git add lib/claimlock/evidence.py lib/claimlock/cli.py tests/test_evidence.py
git commit -F - <<'EOF'
fix(evidence): a locator that cannot discriminate is unlocatable, not resolved

`evidence` reduced a ref to its longest identifier-shaped token and reported
`resolved` if that token appeared anywhere. On a 140-claim store that meant
`shell.test.ts: 'attributes a frame…'` resolved on the word `attributes` (79
files) and another ref on `platform` (1,001 files) — a pass that established
nothing, printing identically to a real check.

A locator found in more than AMBIGUOUS_FILES files is now `unlocatable` with
reason `ambiguous`: claimlock saying it cannot check this ref, which is the state
this module's own docstring already describes as "a different fact from one that
is wrong". It never fails the gate, so no store meets a cliff.

The threshold is 3, and it separates two populations rather than splitting one: a
genuine test name appears in 1 or 2 files, and the lowest common-word locator
measured is 79. The boundary is pinned by a test on both sides of it.

The counting reuses the existing single walk — per FILE, not per occurrence — so
the scan costs what it did.
EOF
```

---

### Task 2: An explicit `<file>::<test>` ref resolves exactly

**Files:**
- Modify: `lib/claimlock/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `Check.reason` from Task 1.
- Produces: `evidence.parse_ref(ref) -> tuple[str, str] | None` returning
  `(rel_file, test_name)` for an explicit ref and `None` for a prose ref;
  `Check.locator` holds the `rel_file::test_name` string for an explicit ref so
  a reader can see what was looked for.

- [ ] **Step 1: Write the failing tests**

```python
    def test_an_explicit_ref_names_a_file_and_a_test(self):
        self.assertEqual(
            E.parse_ref("crates/a/src/main.rs::mod_tests::no_url_refuses"),
            ("crates/a/src/main.rs", "mod_tests::no_url_refuses"))
        self.assertEqual(
            E.parse_ref("packages/web-sdk/src/shell.test.ts::createShell > attributes a frame"),
            ("packages/web-sdk/src/shell.test.ts", "createShell > attributes a frame"))

    def test_a_prose_ref_is_not_an_explicit_ref(self):
        # Falls through to the locator rule rather than being mis-parsed.
        self.assertIsNone(E.parse_ref("shell.test.ts: 'attributes a frame'"))
        self.assertIsNone(E.parse_ref("pkg::mod::a_test_name"))

    def test_a_bare_filename_is_refused(self):
        # Two packages may hold shell.test.ts, so a name with no directory
        # cannot identify one. Spec §4.2.
        self.assertIsNone(E.parse_ref("shell.test.ts::createShell > attributes a frame"))

    def test_an_explicit_ref_whose_file_is_missing_is_unresolved(self):
        proj = self.project_with_files({"src/a.py": "def a_named_test_function(): pass\n"})
        claims = [self.claim("c1", evidence=[{"kind": "test", "ref": "src/nope.py::a_named_test_function"}])]
        checks, _s, _k = E.audit(proj, claims)
        self.assertEqual(checks[0].outcome, "unresolved")

    def test_an_explicit_ref_whose_name_is_absent_from_its_file_is_unresolved(self):
        # The file exists and contains OTHER tests. The point of an explicit ref
        # is that the name is checked in THAT file, not anywhere in the repo.
        proj = self.project_with_files({
            "src/a.py": "def a_named_test_function(): pass\n",
            "src/b.py": "def some_other_test_function(): pass\n",
        })
        claims = [self.claim("c1", evidence=[{"kind": "test", "ref": "src/b.py::a_named_test_function"}])]
        checks, _s, _k = E.audit(proj, claims)
        self.assertEqual(checks[0].outcome, "unresolved")
```

- [ ] **Step 2: Run them and watch them fail**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest tests.test_evidence -v
```
Expected: `AttributeError: module 'claimlock.evidence' has no attribute 'parse_ref'`.

- [ ] **Step 3: Implement `parse_ref`**

```python
def parse_ref(ref):
    """`(rel_file, test_name)` for an explicit `<repo-relative file>::<test>`
    ref, else None.

    A ref qualifies only when the part before the FIRST `::` looks like a path
    with a directory and a file extension. That is what keeps
    `pkg::mod::a_test` — the existing Rust-ish shape — falling through to the
    locator rule, and what refuses a bare `shell.test.ts::…`: two packages may
    hold that filename, so it identifies nothing (spec §4.2)."""
    head, sep, tail = ref.partition("::")
    if not sep or not tail.strip():
        return None
    head = head.strip()
    if "/" not in head or "." not in head.rsplit("/", 1)[1]:
        return None
    if head.startswith("/") or ".." in head.split("/"):
        return None
    return head, tail.strip()
```

- [ ] **Step 4: Resolve an explicit ref against its own file**

In `audit`, split `wanted` into explicit and locator-based before the walk, and
resolve the explicit ones by reading only the file each names:

```python
    explicit, wanted = [], []
    for c in claims:
        for e in c.evidence:
            if not isinstance(e, dict) or e.get("kind") != "test":
                continue
            ref = e.get("ref")
            ref = ref if isinstance(ref, str) else ""
            parsed = parse_ref(ref)
            if parsed is not None:
                explicit.append((c.id, ref, parsed))
            else:
                wanted.append((c.id, ref, locator(ref)))
```

and after the walk (`R.safe_source` is how the rest of the codebase resolves a
repo-relative path without escaping the root — reuse it, do not join by hand):

```python
    for cid, ref, (rel, name) in explicit:
        p = R.safe_source(project.root, rel)
        loc = f"{rel}::{name}"
        if p is None or not p.is_file():
            checks.append(Check(cid, ref, loc, "unresolved"))
            continue
        try:
            text = p.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            checks.append(Check(cid, ref, loc, "unlocatable", "unreadable"))
            continue
        last = name.rsplit("::", 1)[-1].rsplit(" > ", 1)[-1].strip()
        checks.append(Check(cid, ref, loc, "resolved" if last and last in text else "unresolved"))
```

- [ ] **Step 5: Run the new tests, then the whole suite**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest tests.test_evidence -v
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest discover -s tests 2>&1 | tail -3
```
Expected: all pass; suite OK.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/claimlock
git add lib/claimlock/evidence.py tests/test_evidence.py
git commit -F - <<'EOF'
feat(evidence): an explicit <file>::<test> ref is resolved against that file

A ref may now name its test unambiguously as `<repo-relative file>::<test
name>`, which is the shape a person would type to run it and one family with the
existing `::` convention. It resolves against THAT file rather than against the
whole repository, so a name present elsewhere no longer stands in for a name
that is absent where it was cited.

A bare filename with no directory is refused rather than guessed: two packages
may hold `shell.test.ts`. A path escaping the root is refused for the same
reason every other path in this tool goes through `safe_source`.

Prose refs are untouched and still fall through to the locator rule, so nothing
existing changes shape.
EOF
```

---

### Task 3: `matched` is a state of its own

**Files:**
- Modify: `lib/claimlock/evidence.py`
- Modify: `lib/claimlock/cli.py`
- Modify: `README.md`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `parse_ref` and the explicit branch from Task 2.
- Produces: outcome `"matched"` in `Check.outcome`; `cmd_evidence` prints a
  `MATCHED` listing and counts it in the census; no exit-code change.

- [ ] **Step 1: Write the failing tests**

```python
    def test_a_statically_matched_explicit_ref_is_not_resolved(self):
        # The file exists and the name is in it, but no runner was asked. That
        # is weaker than a runner listing the test, and printing the two the
        # same is the defect this whole change is about, in a milder form.
        proj = self.project_with_files({"src/a.py": "def a_named_test_function(): pass\n"})
        claims = [self.claim("c1", evidence=[{"kind": "test", "ref": "src/a.py::a_named_test_function"}])]
        checks, _s, _k = E.audit(proj, claims)
        self.assertEqual(checks[0].outcome, "matched")

    def test_matched_does_not_fail_the_gate(self):
        # cmd_evidence exits 1 for unresolved ONLY.
        self.assertEqual(E.exit_code([E.Check("c", "r", "l", "matched")]), 0)
        self.assertEqual(E.exit_code([E.Check("c", "r", "l", "unlocatable", "ambiguous")]), 0)
        self.assertEqual(E.exit_code([E.Check("c", "r", "l", "unresolved")]), 1)
```

- [ ] **Step 2: Run them and watch them fail**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest tests.test_evidence -v
```
Expected: the first fails with `'resolved' != 'matched'`; the second with
`AttributeError: … has no attribute 'exit_code'`.

- [ ] **Step 3: Implement**

In Task 2's explicit branch, the static hit becomes `"matched"`:

```python
        checks.append(Check(cid, ref, loc, "matched" if last and last in text else "unresolved"))
```

Add the decision function so the CLI and the tests share one rule:

```python
def exit_code(checks):
    """1 when any ref is `unresolved`, else 0. `matched` and `unlocatable` are
    reports, not failures — a store adopting this must not meet a cliff, the
    same reasoning that keeps orphans non-failing."""
    return 1 if any(c.outcome == "unresolved" for c in checks) else 0
```

- [ ] **Step 4: Teach `cmd_evidence` the state**

```python
    matched = [c for c in checks if c.outcome == "matched"]
    _print_capped(matched, cap, "… and {n} more statically matched evidence refs — claimlock evidence --full",
                  lambda c: print(f"MATCHED {c.claim_id}  {c.ref}"))
```

and in the census, between resolved and unresolved:
`f"{len(resolved)} resolved, {len(matched)} matched, {len(unresolved)} unresolved, …"`.
Return `evidence.exit_code(checks)` instead of the inline `1 if unresolved`.

- [ ] **Step 5: Document the four states in the README**

In the section that describes `evidence`, add the table from spec §4.3 verbatim
(`resolved` / `matched` / `unresolved` / `unlocatable` and what each means), plus
one sentence: only `unresolved` fails. `tests/test_plugin_manifest.py` requires
the README to name every command; a new state is not a command, but the table is
what stops `matched` reading as a weaker `resolved` to someone who never sees
this plan.

- [ ] **Step 6: Run the suite**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest discover -s tests 2>&1 | tail -3
cd ~/projects/claimlock && FORCE_COLOR= python3 bin/claimlock self-test | tail -1
```
Expected: OK; `SELF-TEST: all 19 checks passed`.

- [ ] **Step 7: Commit**

```bash
cd ~/projects/claimlock
git add lib/claimlock/evidence.py lib/claimlock/cli.py README.md tests/test_evidence.py
git commit -F - <<'EOF'
feat(evidence): a static match is `matched`, never `resolved`

An explicit ref whose file exists and whose test name appears in it is now
`matched`, not `resolved`. The distinction is the whole point: a static hit
cannot see a test name built in a loop — `for (const [e, w] of …) it(`${e}: …`)`
exists in the store this was measured against — so calling it `resolved` would
reproduce, in a milder form, the same defect as resolving on a common word.

`resolved` is now reserved for a runner listing the test, which arrives next.
`matched` and `unlocatable` never fail; `unresolved` still does, and
`evidence.exit_code` is now the one place that says so, shared by the CLI and
its tests.
EOF
```

---

### Task 4: `--ask-runners` earns `resolved` from the runner itself

**Files:**
- Create: `lib/claimlock/runners.py`
- Create: `tests/test_runners.py`
- Modify: `lib/claimlock/evidence.py`
- Modify: `lib/claimlock/cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `Check`, `parse_ref`, `exit_code` from Tasks 1–3.
- Produces: `runners.vitest_tests(root, rel_file) -> set[tuple[str, str]] | None`
  and `runners.cargo_tests(root, rel_file) -> set[tuple[str, str]] | None`, each
  returning `(rel_file, test_name)` pairs or `None` when that runner cannot be
  consulted; `audit(project, claims, globs=None, ask_runners=False)`.

- [ ] **Step 1: Write the failing tests for the pure parts**

The subprocess is not the interesting part; the parsing and the package-root
walk are. Test those directly.

```python
class Runners(unittest.TestCase):
    def test_vitest_json_becomes_rel_file_and_name_pairs(self):
        # vitest's JSON gives an ABSOLUTE file and a name WITHOUT the file. Both
        # have to be normalised before comparing, and getting either wrong makes
        # every ref unresolved.
        root = Path("/repo")
        raw = json.dumps([
            {"name": "createShell > attributes a frame", "file": "/repo/packages/web-sdk/src/shell.test.ts"},
            {"name": "boogyDev plugin > serves", "file": "/repo/packages/web-sdk/dev/plugin.test.ts"},
        ])
        self.assertEqual(
            R.parse_vitest_json(raw, root),
            {("packages/web-sdk/src/shell.test.ts", "createShell > attributes a frame"),
             ("packages/web-sdk/dev/plugin.test.ts", "boogyDev plugin > serves")})

    def test_vitest_json_that_is_not_json_yields_nothing_rather_than_raising(self):
        self.assertEqual(R.parse_vitest_json("not json at all", Path("/repo")), set())

    def test_the_package_root_is_the_nearest_ancestor_with_vitest(self):
        # A monorepo has a root package.json too; the list must run in the
        # package that actually has vitest, or it lists the wrong project.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text('{"name":"monorepo"}')
            pkg = root / "packages" / "web-sdk"
            (pkg / "src").mkdir(parents=True)
            (pkg / "package.json").write_text('{"devDependencies":{"vitest":"^4"}}')
            (pkg / "src" / "a.test.ts").write_text("")
            self.assertEqual(R.vitest_package_root(root, "packages/web-sdk/src/a.test.ts"), pkg)

    def test_no_ancestor_has_vitest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text('{"name":"monorepo"}')
            (root / "a.test.ts").write_text("")
            self.assertIsNone(R.vitest_package_root(root, "a.test.ts"))

    def test_cargo_list_output_becomes_names(self):
        # `--list` prints "path::to::test: test" lines; anything else is noise.
        out = "db::fdb::ledger::codec_tests::a_case: test\nbenches: benchmark\n"
        self.assertEqual(R.parse_cargo_list(out), {"db::fdb::ledger::codec_tests::a_case"})
```

- [ ] **Step 2: Run them and watch them fail**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest tests.test_runners -v
```
Expected: `ModuleNotFoundError: No module named 'claimlock.runners'`.

- [ ] **Step 3: Implement `runners.py`**

```python
"""Ask a test runner what tests exist. The ONLY subprocess in claimlock.

Nothing from a claim reaches a command line. A ref contributes a repo-relative
PATH, which is validated by the caller through `refs.safe_source` before it gets
here, and a test NAME, which is only ever compared in Python against what a
runner printed. Every argv below is fixed.
"""
import json
import subprocess
from pathlib import Path

TIMEOUT = 120


def parse_vitest_json(raw, root):
    """`{(rel_file, name)}` from `vitest list --json`. `file` is absolute and
    `name` excludes the file, so both are normalised here rather than at the
    comparison, where a mistake would silently make every ref unresolved."""
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    out = set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        f, n = it.get("file"), it.get("name")
        if not isinstance(f, str) or not isinstance(n, str):
            continue
        try:
            out.add((Path(f).resolve().relative_to(Path(root).resolve()).as_posix(), n))
        except ValueError:
            continue          # outside the repo — not ours to claim
    return out


def vitest_package_root(root, rel_file):
    """The nearest ancestor directory of `rel_file` whose package.json mentions
    vitest, or None. A monorepo root usually has a package.json WITHOUT vitest,
    and listing from there lists the wrong project."""
    root = Path(root)
    d = (root / rel_file).parent
    while True:
        pj = d / "package.json"
        if pj.is_file():
            try:
                if "vitest" in pj.read_text(encoding="utf-8"):
                    return d
            except OSError:
                pass
        if d == root:
            return None
        d = d.parent


def parse_cargo_list(out):
    """Test paths from `cargo test -- --list` (`a::b::c: test` lines)."""
    names = set()
    for line in out.splitlines():
        name, sep, kind = line.rpartition(": ")
        if sep and kind.strip() == "test" and name.strip():
            names.add(name.strip())
    return names
```

Then the two consulting functions, each returning `None` when it cannot run —
a runner that is absent is not a failing claim:

```python
def vitest_tests(root, rel_file):
    pkg = vitest_package_root(root, rel_file)
    if pkg is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "vitest-list.json"      # NEVER a path from a ref:
        try:                                      # `--json <path>` WRITES there,
            subprocess.run(["npx", "vitest", "list", "--json", str(out)],
                           cwd=pkg, capture_output=True, text=True,
                           timeout=TIMEOUT, check=False)
            return parse_vitest_json(out.read_text(encoding="utf-8"), root)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
```

Add `import tempfile` at the top. The comment about `--json` is load-bearing:
during this feature's investigation, `vitest list --json src/shell.test.ts` was
read by vitest as "write the report to that path" and truncated a 251-line test
file. A report path is always one claimlock created.

`cargo_tests(root, rel_file)` is the same shape: find the nearest ancestor with a
`Cargo.toml`, run `["cargo", "test", "--all-targets", "--", "--list"]` there with
`capture_output`, and return `{(rel_file, name) for name in parse_cargo_list(out.stdout)}`
— the rel_file is the one asked about, since `--list` does not report files.

- [ ] **Step 4: Wire the flag**

`audit` gains `ask_runners=False`. In the explicit branch, when it is set and the
file's suffix says which runner to ask (`.ts`/`.js`/`.tsx` → vitest, `.rs` →
cargo), consult the runner ONCE PER FILE (cache by `rel`), and:

- runner returned `None` → keep the Task 3 static outcome (`matched`)
- the `(rel, name)` pair is in the runner's set → `resolved`
- the runner ran and does not list it → `unresolved`

In `cli.py`, add to the `evidence` subparser:

```python
    sp.add_argument("--ask-runners", action="store_true",
                    help="consult vitest/cargo so a matched ref can become resolved (runs subprocesses)")
```

and pass `ask_runners=args.ask_runners`.

- [ ] **Step 5: Run the suite and the self-test**

```
cd ~/projects/claimlock && FORCE_COLOR= python3 -m unittest discover -s tests 2>&1 | tail -3
cd ~/projects/claimlock && FORCE_COLOR= python3 bin/claimlock self-test | tail -1
```
Expected: OK; 19/19.

- [ ] **Step 6: Exercise it against the real store, both ways**

```
cd ~/projects/boogy/.claude/worktrees/oauth-connections
CL=~/.claude/plugins/cache/claimlock/claimlock/0.1.0/bin/claimlock
$CL evidence | tail -2                # static: matched, no subprocess
$CL evidence --ask-runners | tail -2  # asks vitest and cargo
```

Expected: the second promotes at least the vitest refs from `matched` to
`resolved` or demotes them to `unresolved` — either is a real answer, and which
one it is belongs in the commit message. **If nothing changes between the two
lines, the flag did nothing and the task is not done**: check that
`vitest_package_root` found `packages/web-sdk` and that the JSON parsed.

- [ ] **Step 7: Document and commit**

Add `--ask-runners` to the README's `evidence` entry, noting it runs subprocesses
and is therefore opt-in, and that `check` never does.

```bash
cd ~/projects/claimlock
git add lib/claimlock/runners.py lib/claimlock/evidence.py lib/claimlock/cli.py README.md tests/test_runners.py
git commit -F - <<'EOF'
feat(evidence): --ask-runners earns `resolved` from the runner itself

`claimlock evidence --ask-runners` asks vitest (`list --json`) and cargo
(`test --all-targets -- --list`) what tests exist, so an explicit ref can be
`resolved` by the runner rather than `matched` by a substring. Opt-in, and never
reachable from `check`: its baseline is 0.096 s and it runs in every hook.

Nothing from a claim reaches a command line. Every argv is fixed; a ref
contributes a path already validated through `safe_source` and a name only ever
compared in Python. The report path handed to `vitest --json` is always one
claimlock created in a temporary directory — that flag WRITES to its argument,
and during this feature's investigation pointing it at a source path truncated a
251-line test file.

A runner that cannot be consulted returns None and leaves the static outcome
alone: an absent toolchain is not a false claim.
EOF
```

---

## Self-review

**1. Spec coverage.** §4.1 → Task 1 (threshold, `ambiguous` reason, census).
§4.2 → Task 2 (`parse_ref`, bare-filename refusal, file-scoped resolution).
§4.3 → Task 3 (`matched`, the README table, `exit_code` as one rule).
§4.4 → Task 4 (per-language runners, opt-in flag, the untrusted-input and
`--json`-writes-there constraints). §4.5 migration is covered by Task 1's
non-failing `unlocatable` and Task 3's `exit_code`. §5's exclusions are in Global
Constraints (`kind: test` only) and in the plan's scope note. No gap found.

**2. Placeholders.** None: every step names its command and shows its code. The
one prose-only step is Task 3's README table, whose content is "the table from
spec §4.3 verbatim" — a specific artifact, not a TBD. Task 4 Step 3 describes
`cargo_tests` in prose rather than code; it is the same shape as `vitest_tests`
directly above it, with the two differences named (manifest file, argv). Left as
prose deliberately so the implementer writes it against the real `cargo --list`
output rather than transcribing an argv I have not run in that repo.

**3. Type consistency.** `Check(claim_id, ref, locator, outcome, reason=None)` is
introduced in Task 1 and used with that arity in Tasks 2–4. `parse_ref` returns
`tuple[str, str] | None` in Task 2 and is consumed as such in Task 4.
`vitest_tests`/`cargo_tests` return `set[tuple[str, str]] | None` in their
Interfaces block and are used that way in Step 4. `exit_code(checks) -> int`
appears in Task 3 and is called in Task 3 Step 4 only. `audit`'s return stays
`([Check], scanned, skipped)` throughout; only its parameters grow.
