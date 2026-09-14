# End-to-end acceptance — 2026-09-14

Observed with `claude --version` = `2.1.270 (Claude Code)`.

## `claude plugin validate`

```
$ claude plugin validate ~/projects/claimlock
Validating marketplace manifest: ~/projects/claimlock/.claude-plugin/marketplace.json

✔ Validation passed
```

## Scratch project

A throwaway project built with `mktemp -d` (never inside this repo):

```
SCRATCH=$(mktemp -d)                 # <scratch>
cd "$SCRATCH/work" && git init -q
git config user.email acc@example.com && git config user.name acceptance
mkdir -p src
cat > src/limit.py <<'EOF'
MAX = 5


def clamp(n):
    return min(n, MAX)
EOF
python3 ~/projects/claimlock/bin/claimlock init
# claims/retries-are-capped.md: sources: [path: src/limit.py], evidence: [kind: run, ...]
python3 ~/projects/claimlock/bin/claimlock verify retries-are-capped
git add -A && git commit -q -m "initial: capped retries claim"
```

`claimlock init` created `.claimlock.toml`, `claims/`, `.gitignore (+ .claimlock/)`.
`claimlock verify retries-are-capped` reported `verified retries-are-capped` /
`src/limit.py @ f4f9057edb2d`, exit 0. Initial commit `12617fe`.

## Headless session, run 1 — literal brief command (found a shell-quoting bug in the command itself)

```bash
cd "$SCRATCH/work"
timeout 180 claude -p "1) Quote any line in your context starting with 'claimlock:'. 2) Run: sh -c 'sed -i s/MAX = 5/MAX = 9/ src/limit.py && git commit -qam bump'. 3) Quote any new 'claimlock:' line you can now see. 4) Run: claimlock check" \
  --plugin-dir ~/projects/claimlock --output-format stream-json --verbose \
  --allowedTools "Bash(claimlock:*)" "Bash(sh:*)" "Bash(git:*)" \
  > "$SCRATCH/accept.jsonl" 2>&1
```

Exit 0. **Deviation from the brief's literal Step 2 sub-command, recorded here
since it changed what the run could show:** the brief's `sed -i s/MAX = 5/MAX
= 9/ src/limit.py` is unquoted inside the outer `sh -c '...'`, so the shell
that `sh -c` runs splits it on the spaces around `=` before sed ever sees it
— sed receives `s/MAX` as its script and dies immediately. Claude ran exactly
that command (verbatim, as instructed) and got:

```
Exit code 1
sed: -e expression #1, char 5: unterminated 's' command
```

Because of the `&&`, `git commit` never ran; `src/limit.py` still read `MAX =
5`, the tree was clean, HEAD was still `12617fe`. Claude correctly diagnosed
this in its final message ("The sed pattern isn't quoted inside the `sh -c
'...'` string, so the shell splits it at the spaces... Because of the `&&`,
`git commit` never ran"), then ran `claimlock check` anyway (exit 0, "0
stale") — a faithful report of a no-op, not a false positive. This is a bug
in the acceptance command's own shell quoting, not a claimlock defect (the
same category of finding as the `--allowedTools` positional-argument order
issue recorded in `docs/hook-semantics.md`); it just means run 1 exercised
nothing past Step 1. Fixed for run 2 below by quoting the sed script:
`sed -i "s/MAX = 5/MAX = 9/" src/limit.py`.

No `hook-errors.log` was written for run 1 either (checked after run 2 below,
which covers both sessions' data).

## Headless session, run 2 — fixed quoting, full acceptance evidence

```bash
cd "$SCRATCH/work"
timeout 180 claude -p "1) Quote any line in your context starting with 'claimlock:'. 2) Run: sh -c 'sed -i \"s/MAX = 5/MAX = 9/\" src/limit.py && git commit -qam bump'. 3) Quote any new 'claimlock:' line you can now see. 4) Run: claimlock check" \
  --plugin-dir ~/projects/claimlock --output-format stream-json --verbose \
  --allowedTools "Bash(claimlock:*)" "Bash(sh:*)" "Bash(git:*)" \
  > "$SCRATCH/accept2.jsonl" 2> "$SCRATCH/accept2.stderr"
```

Exit 0, empty stderr, 39 stream-json records. Note: the assistant's first
attempt at Step 2 chained `; echo "exit=$?"; git log --oneline -2` onto the
command and headless mode denied it (`permission_denied`: "This Bash command
contains multiple operations. The following part requires approval: echo
\"exit=$?\"") — expected behavior of the allowlist, not a claimlock concern;
Claude retried with just the single approved command and it ran.

| # | Check | PASS/FAIL | Evidence (verbatim) |
|---|---|---|---|
| 1 | SessionStart context reached Claude | **PASS** | First assistant text block: `"Step 1 — the \`claimlock:\` line in my context:\n\n> claimlock: 1 claims, all fresh. Before asserting a limit, default or guarantee, run \`claimlock search <topic>\`. A non-fresh claim is owed a re-check (\`claimlock diff <id>\`), never a bare re-stamp."` The raw hook payload (`hook_response`, `hook_name: "SessionStart:startup"`) carried exactly `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "claimlock: 1 claims, all fresh. ..."}}`. |
| 2 | Commit via `sh -c` detected | **PASS** | Third assistant text block (Step 3): `"After the commit, a new \`claimlock:\` line appeared: *\"claimlock: HEAD moved 12617fe→48669af (a commit, merge, rebase, pull or checkout). Files changed in that range back 1 claim(s) that are no longer fresh: retries-are-capped (stale). Re-check each with \`claimlock diff <id>\`; \`claimlock verify <id>\` only after re-checking.\"*"` — this is the PostToolUse hook's `additionalContext`, delivered into context after the `Bash` tool call that ran the sed+commit (which itself produced commit `48669af`, "bump"). |
| 3 | Gate fails after drift | **PASS** | Step-4 `Bash` tool_result, verbatim: `"Exit code 1\nSTALE    retries-are-capped\n         src/limit.py: stale\n         re-check it (claimlock diff retries-are-capped), then: claimlock verify retries-are-capped\nclaimlock: 1 claims, 1 sources hashed — 0 invalid, 0 unpinned, 1 stale, 0 missing"` — contains `STALE    retries-are-capped` and a non-zero exit. |
| 4 | Stop warned the user, did not continue the turn | **PASS** | CLI-side display line (`type: "system", subtype: "informational"`): `"Stop says: claimlock: this session introduced 1 stale (retries-are-capped). Inspect with \`claimlock diff <id>\` or \`claimlock refs\`."` This is **not** an assistant message — the transcript's last `assistant` text block (the Step 1–4 recap ending "Should I do the re-check?") precedes it, and the final `type: "result"` record has `"stop_reason": "end_turn"`, `"num_turns": 4`, and a `"result"` string byte-identical to that last assistant text — i.e. no further model turn ran after the Stop hook fired. |
| 5 | No hook error | **PASS** | `find ~/.claude/plugins/data/claimlock-inline -iname hook-errors.log` after both runs: no match, empty output, exit 0. The data dir held only `sessions/<session-id>.{json,lock}` for the two sessions (confirming, incidentally, the README's "lock files accumulate" limit: both sessions' `.lock` files are still present). |

**Tally: 5/5 PASS** (using the shell-quoting fix noted above; the literal
brief command in run 1 is a documented pre-existing gotcha in the example
command's own quoting, not a finding about claimlock).

## Full test suite (this session, before the acceptance runs)

```
$ python3 -m unittest discover -s tests -v
...
Ran 101 tests in 7.437s

OK
```

(93 pre-existing + this task's README doc test + 5 collaboration tests = 99;
101 observed because a concurrent Task 9 fix — scoped to `skills/*`,
`tests/skills/*`, `tests/test_skills_format.py`,
`lib/claimlock/importer.py`'s docstring — added tests of its own in this
working tree at the same time. 0 skipped.)
