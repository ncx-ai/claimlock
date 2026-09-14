# Hook semantics — observed

Observed 2026-09-14T19:39:01Z with `claude --version` = `2.1.270 (Claude Code)`.
Probe: a throwaway plugin emitting one token per event (code reproduced in
docs/plans/2026-09-14-claimlock/01-hook-semantics-spike.md). Built and run
entirely under a `mktemp -d` scratch directory (`<scratch>`), never
inside this repo; nothing from it is committed.

Machine-specific paths (the scratch directory, and the local plugin-data
directory) have been redacted below to placeholders (`<scratch>`, a
`~`-relative path); nothing else in the quoted output was changed.

## Step 1 — CLI support

`claude --help 2>&1 | grep -iE "plugin-dir|output-format|allowedTools|verbose"`
showed `--plugin-dir <path>`, `--output-format <format>`, `--allowedTools,
--allowed-tools <tools...>`, and `--verbose` are all present, so no substitute
loading mechanism was needed.

**Deviation from the brief's literal Step 3 command (recorded here since it
cost a full run):** `--allowedTools <tools...>` is *variadic* (commander.js
style) and, run exactly as written —
`--allowedTools "Bash(echo:*)" "Bash(ls:*)" "<prompt text>"` — it swallows the
trailing prompt string as a third "tool", leaving no positional prompt
argument. The literal command exited 1 with:

```
Error: Input must be provided either through stdin or as a prompt argument when using --print
```

Fix used for every run below: put the prompt positional *before* the
variadic `--allowedTools` flag, e.g.
`claude -p "<prompt>" --plugin-dir ... --output-format stream-json --verbose --allowedTools "Bash(echo:*)" "Bash(ls:*)"`.
This is a CLI argument-parsing fact, not one of F1–F6, but Task 8 (or anyone
re-running this probe) needs it to avoid the same false start.

## Step 3/4 — main run

```
cd "$SCRATCH/work" && git init -q && git config user.email p@example.com && git config user.name p
claude -p "First: list every token in your context that starts with PROBE-TOKEN, verbatim, or say NONE-AT-START. Then run the shell command: echo hi. Then list every PROBE-TOKEN you can now see, verbatim, or say NONE-AFTER-TOOL." \
  --plugin-dir "$SCRATCH/probe-plugin" --output-format stream-json --verbose \
  --allowedTools "Bash(echo:*)" "Bash(ls:*)" \
  > "$SCRATCH/run1.jsonl" 2> "$SCRATCH/run1.stderr"
```

Exit 0, empty stderr. `grep -o 'PROBE-TOKEN-[A-Z-]*[0-9]*' run1.jsonl | sort | uniq -c`:

```
      2 PROBE-TOKEN-POSTTOOL-8823
      5 PROBE-TOKEN-SESSIONSTART-4417
      1 PROBE-TOKEN-STOP-USERONLY-5501
```

`probe-log.jsonl` was **not** under `~/.claude/plugins/data/*probe*` by simple
glob on the plugin's declared name; `find / -name probe-log.jsonl` located it
at `~/.claude/plugins/data/claimlock-probe-inline/probe-log.jsonl`
— a `--plugin-dir`-loaded (as opposed to marketplace-installed) plugin gets an
`-inline` suffix appended to its `plugin.json` name for its data-dir slug.
Record this for Task 8: don't assume the data dir is named exactly after the
plugin.

| # | Fact | Verdict | Evidence (command + observed output, verbatim excerpt) |
|---|---|---|---|
| F1 | SessionStart `additionalContext` reaches the model | CONFIRMED | First assistant message text, before any tool call: `"Tokens starting with PROBE-TOKEN in my context right now:\n\n- \`PROBE-TOKEN-SESSIONSTART-4417\`"` — extracted from `run1.jsonl` via the brief's Python scan of `type == "assistant"` messages. |
| F2 | Matcher `Bash\|mcp__.*` fires PostToolUse for Bash | CONFIRMED | `probe-log.jsonl` entry: `{"event": "post-tool-use", "payload": {..., "tool_name": "Bash", "hook_event_name": "PostToolUse", "tool_input": {"command": "echo hi", ...}, ...}}` — exactly one such entry for this session. |
| F3 | PostToolUse `additionalContext` reaches the model | CONFIRMED | Second (post-tool) assistant message text: `"The command printed \`hi\`.\n\nThese are the PROBE-TOKENs I can see now:\n\n- \`PROBE-TOKEN-SESSIONSTART-4417\` (from the SessionStart hook, there from the start)\n- \`PROBE-TOKEN-POSTTOOL-8823\` (added by the PostToolUse hook after the command ran)"`. |
| F4 | Stop `systemMessage` is NOT given to the model and does NOT continue the turn | CONFIRMED | The **only** occurrence of `STOP-USERONLY` in `run1.jsonl` is a CLI-side display line, not model content: `{"type":"system","subtype":"informational","content":"Stop says: PROBE-TOKEN-STOP-USERONLY-5501","level":"notice", ...}`. No `type == "assistant"` message anywhere in the transcript contains the token (checked by printing every assistant text block in full — only the two quoted above exist). The final `type == "result"` record has `"num_turns": 2`, `"stop_reason": "end_turn"`, and `"result"` text byte-identical to the second assistant message — i.e. no further model turn ran after Stop fired. `probe-log.jsonl` has exactly one `event: "stop"` entry for this session, with `payload.stop_hook_active: false`. |
| F5 | `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR` set in hook env; Stop payload has `session_id` + `stop_hook_active` | CONFIRMED | Every `probe-log.jsonl` entry's `env` field: `{"CLAUDE_PLUGIN_ROOT": "<scratch>/probe-plugin", "CLAUDE_PLUGIN_DATA": "~/.claude/plugins/data/claimlock-probe-inline", "CLAUDE_PROJECT_DIR": "<scratch>/work"}` (all non-null on every event, including `session-start`). The `stop` entry's `payload`: `{"session_id": "79690432-1d4a-4e41-82c0-7c1354c15077", "stop_hook_active": false, "hook_event_name": "Stop", ...}`. |
| F6 | A plugin's `bin/` is on the Bash tool PATH | CONFIRMED | Rerun: `claude -p "run the shell command: probe-bin" --plugin-dir "$SCRATCH/probe-plugin" --output-format stream-json --verbose --allowedTools "Bash(probe-bin:*)"` (with `$SCRATCH/probe-plugin/bin/probe-bin` containing `#!/bin/sh\necho PROBE-BIN-OK`, chmod +x). Transcript `tool_use` block: `{"type": "tool_use", "name": "Bash", "input": {"command": "probe-bin", ...}}` — the bare command, no path. Matching `tool_result`: `{"tool_use_id": "...", "type": "tool_result", "content": "PROBE-BIN-OK", "is_error": false}`. The Bash tool resolved a bare `probe-bin` invocation to the plugin's `bin/` directory. |

Hook process cost (python3 start + trivial body), median of 3:
`time (echo '{}' | python3 "$SCRATCH/probe-plugin/hooks/probe.py" post-tool-use >/dev/null)`
gave `real 0m0.047s` on all three runs (0.047s, 0.047s, 0.047s) — **median 47 ms**.

## Consequences for the design

All six facts CONFIRMED with transcript/log evidence (none REFUTED, none
UNOBSERVABLE). One process note for whoever re-runs this probe or writes
Task 8's own headless test harness: `--allowedTools` is variadic and will eat
a trailing prompt positional if the prompt is placed after it on the command
line — put the prompt argument before `--allowedTools`, or terminate the
flag's argument list some other way. No change to Task 8's hook design itself
is required by this spike.
