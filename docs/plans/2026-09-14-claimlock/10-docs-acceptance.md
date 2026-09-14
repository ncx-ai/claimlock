### Task 10: README, format reference, license, end-to-end acceptance

**Files:**
- Create: `README.md`, `docs/format.md`, `LICENSE`, `docs/acceptance-2026-09-14.md`
- Modify: `tests/test_plugin_manifest.py` (add a README check)

**Interfaces:**
- Consumes: everything.
- Produces: an installable plugin with observed evidence that it works inside a real Claude Code session.

- [ ] **Step 1: Failing doc test**

Append to `tests/test_plugin_manifest.py` class `Manifest`:
```python
    def test_readme_documents_install_and_every_command(self):
        import re, subprocess, sys
        from helpers import BIN
        readme = (REPO / "README.md").read_text()
        self.assertIn("/plugin marketplace add", readme)
        self.assertIn("/plugin install claimlock@claimlock", readme)
        help_text = subprocess.run([sys.executable, str(BIN), "--help"], capture_output=True, text=True).stdout
        for cmd in re.findall(r"^\s{4}([a-z-]+)\s", help_text, re.M):
            self.assertIn(f"claimlock {cmd}", readme, cmd)
        self.assertTrue((REPO / "LICENSE").read_text().startswith("MIT License"))
        self.assertTrue((REPO / "docs/format.md").is_file())
```
Run → FAIL (`FileNotFoundError: README.md`).

- [ ] **Step 2: `LICENSE`**

Standard MIT text, first line `MIT License`, `Copyright (c) 2026 dg`.

- [ ] **Step 3: `docs/format.md`**

The normative claim format, derived from the implementation (read `claims.py` and `frontmatter.py`, do not restate the spec from memory): the accepted YAML subset with one example per shape, each field with its rule, the four freshness states and precedence, the invalid rules (list every message prefix `problems()` can emit), the blob formula, `.claimlock.toml` keys and defaults, the marker pattern and scan exclusions, exit codes.

- [ ] **Step 4: `README.md`**

Sections, in this order:
1. **One paragraph:** what it is ("A Claude Code plugin that pins written claims about a codebase to the content of the files that could falsify them…") and the failure it prevents (docs that keep asserting things that stopped being true, with nothing to catch it).
2. **Install:** `/plugin marketplace add <owner>/claimlock`, `/plugin install claimlock@claimlock`; requirement Python ≥ 3.11; git optional.
3. **Thirty-second tour:** `claimlock init`, `claimlock new`, edit, `claimlock verify`, change a source, `claimlock check` fails, `claimlock diff`.
4. **Commands:** a table with every command (`init new check stale list search show verify diff refs affected import self-test hook`), each written as `claimlock <cmd>`.
5. **Hooks:** the three-row table (who sees it, when), the never-blocks guarantee, inert without a store.
6. **Skills:** the four skill names with one line each.
7. **How staleness works:** blob pins, why not timestamps, git optional, snapshots for `diff`, stat cache and its 2 s guard.
8. **CI:** `claimlock self-test && claimlock check && claimlock refs`.
9. **Limits:** file-level granularity (whitespace edits stale claims), pin conflicts on merge, remote-only commits not seen until pulled.

- [ ] **Step 5: Run the suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK`, `Ran 80 tests`.

- [ ] **Step 6: End-to-end acceptance in a real session**

Create a scratch project (`mktemp -d`): `git init`, a source file `src/limit.py` containing `MAX = 5` and a `clamp` function using it, `claimlock init`, a claim `retries-are-capped` citing `src/limit.py` with a `run` evidence entry, `claimlock verify retries-are-capped`, commit. Then run a headless session with the plugin loaded (the flag Task 1 confirmed, expected `--plugin-dir <repo>`):

```bash
claude -p --plugin-dir <claimlock-repo> --output-format stream-json --verbose \
  --allowedTools "Bash(claimlock:*)" "Bash(sh:*)" "Bash(git:*)" \
  "1) Quote any line in your context starting with 'claimlock:'. 2) Run: sh -c 'sed -i s/MAX = 5/MAX = 9/ src/limit.py && git commit -qam bump'. 3) Quote any new 'claimlock:' line you can now see. 4) Run: claimlock check" \
  > "$SCRATCH/accept.jsonl" 2>&1
```

Observe and record in `docs/acceptance-2026-09-14.md` (verbatim excerpts, the `claude --version`, and PASS/FAIL per row):

| Check | PASS when |
|---|---|
| SessionStart context reached Claude | Step 1 quotes `claimlock: 1 claims, all fresh` |
| Commit via `sh -c` detected | Step 3 quotes `HEAD moved` and `retries-are-capped (stale)` |
| Gate fails after drift | Step 4 output contains `STALE    retries-are-capped` and exit 1 |
| Stop warned the user, did not continue the turn | stream shows the Stop hook's `systemMessage` with `introduced 1 stale` and no further assistant turn after it |
| No hook error | `hook-errors.log` absent in the plugin data dir |

Also run `claude plugin validate <claimlock-repo>` if that subcommand exists (`claude plugin --help`); record its output.

Any FAIL: report DONE_WITH_CONCERNS with the row and excerpt; do not paper over it.

- [ ] **Step 7: Commit**

```bash
git add README.md LICENSE docs/format.md docs/acceptance-2026-09-14.md tests/test_plugin_manifest.py
git commit -m "docs: README, format reference, license, and real-session acceptance record" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```
