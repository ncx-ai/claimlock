### Task 1: Hook-semantics spike (throwaway probe, recorded findings)

The spec's hook design rests on documented behaviour that has never been
observed. Before building on it, observe it. The probe code is throwaway; the
**findings file is the deliverable**.

**Files:**
- Create (throwaway, NOT committed): `$SCRATCH/probe-plugin/.claude-plugin/plugin.json`, `$SCRATCH/probe-plugin/hooks/hooks.json`, `$SCRATCH/probe-plugin/hooks/probe.py`
- Create (committed): `docs/hook-semantics.md`

`$SCRATCH` = a fresh `mktemp -d`.

**Interfaces:**
- Consumes: nothing.
- Produces: `docs/hook-semantics.md` — a table of six facts, each marked CONFIRMED / REFUTED / UNOBSERVABLE with the command and the observed output. Task 8 reads it; if any fact is REFUTED, stop and report to the controller before Task 8.

- [ ] **Step 1: Confirm the CLI can load a local plugin headlessly**

Run: `claude --help 2>&1 | grep -iE "plugin-dir|output-format|allowedTools|verbose"`
Expected: lines for `--plugin-dir`, `--output-format`, `--allowedTools` (or `--allowed-tools`), `--verbose`. If `--plugin-dir` is absent, record that and use `claude plugin validate`/`/plugin marketplace add <path>` instructions from `claude plugin --help` instead; note the substitute in the findings.

- [ ] **Step 2: Write the probe plugin**

`$SCRATCH/probe-plugin/.claude-plugin/plugin.json`:
```json
{"name": "claimlock-probe", "description": "throwaway hook probe", "version": "0.0.0"}
```

`$SCRATCH/probe-plugin/hooks/probe.py`:
```python
#!/usr/bin/env python3
"""Emit one distinguishable token per hook event and log every invocation."""
import json, os, sys, time
event = sys.argv[1]
payload = sys.stdin.read()
log = os.path.join(os.environ.get("CLAUDE_PLUGIN_DATA") or "/tmp", "probe-log.jsonl")
os.makedirs(os.path.dirname(log), exist_ok=True)
with open(log, "a") as f:
    f.write(json.dumps({"t": time.time(), "event": event, "payload": json.loads(payload or "{}"),
                        "env": {k: os.environ.get(k) for k in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR")}}) + "\n")
if event == "session-start":
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": "PROBE-TOKEN-SESSIONSTART-4417"}}))
elif event == "post-tool-use":
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": "PROBE-TOKEN-POSTTOOL-8823"}}))
elif event == "stop":
    print(json.dumps({"systemMessage": "PROBE-TOKEN-STOP-USERONLY-5501"}))
sys.exit(0)
```

`$SCRATCH/probe-plugin/hooks/hooks.json`:
```json
{
  "hooks": {
    "SessionStart": [{"matcher": "startup|resume|clear|compact",
      "hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/probe.py\" session-start"}]}],
    "PostToolUse": [{"matcher": "Bash|mcp__.*",
      "hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/probe.py\" post-tool-use"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/probe.py\" stop"}]}]
  }
}
```

- [ ] **Step 3: Run a headless session that exercises all three hooks**

```bash
cd "$SCRATCH" && mkdir work && cd work && git init -q && git config user.email p@example.com && git config user.name p
claude -p --plugin-dir "$SCRATCH/probe-plugin" --output-format stream-json --verbose \
  --allowedTools "Bash(echo:*)" "Bash(ls:*)" \
  "First: list every token in your context that starts with PROBE-TOKEN, verbatim, or say NONE-AT-START. Then run the shell command: echo hi. Then list every PROBE-TOKEN you can now see, verbatim, or say NONE-AFTER-TOOL." \
  > "$SCRATCH/run1.jsonl" 2>&1
```

- [ ] **Step 4: Read the evidence — each fact from the transcript, not from the model's prose alone**

```bash
grep -o 'PROBE-TOKEN-[A-Z-]*[0-9]*' "$SCRATCH/run1.jsonl" | sort | uniq -c
python3 - "$SCRATCH/run1.jsonl" <<'EOF'
import json, sys
for line in open(sys.argv[1]):
    try: o = json.loads(line)
    except ValueError: continue
    t = o.get("type"); st = o.get("subtype")
    if t in ("system",) or "hook" in json.dumps(o)[:400].lower():
        print(t, st, json.dumps(o)[:300])
    if t == "assistant":
        for c in o.get("message", {}).get("content", []):
            if c.get("type") == "text": print("ASSISTANT:", c["text"][:400])
EOF
cat "$(ls -d ~/.claude/plugins/data/*probe* 2>/dev/null | head -1)/probe-log.jsonl" 2>/dev/null || find / -name probe-log.jsonl 2>/dev/null | head -3
```

Record, for each fact, CONFIRMED / REFUTED / UNOBSERVABLE:

| # | Fact | How to decide |
|---|---|---|
| F1 | SessionStart `additionalContext` reaches the model | Assistant text quotes `PROBE-TOKEN-SESSIONSTART-4417` before the tool call |
| F2 | Matcher `Bash\|mcp__.*` fires PostToolUse for Bash | Log has a `post-tool-use` entry whose payload `tool_name` is `Bash` |
| F3 | PostToolUse `additionalContext` reaches the model | Assistant text after the tool quotes `PROBE-TOKEN-POSTTOOL-8823` |
| F4 | Stop `systemMessage` is NOT given to the model and does NOT continue the turn | Stop token never appears in any assistant text; exactly one Stop log entry; session ends |
| F5 | `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR` are set in hook env; Stop payload contains `session_id` and `stop_hook_active` | Log `env` and `payload` fields |
| F6 | A plugin's `bin/` is on the Bash tool PATH | Add `$SCRATCH/probe-plugin/bin/probe-bin` (`#!/bin/sh\necho PROBE-BIN-OK`, chmod +x), rerun with prompt "run the shell command: probe-bin" and `--allowedTools "Bash(probe-bin:*)"`; transcript tool result shows `PROBE-BIN-OK` |

Also measure hook cost: `time (echo '{}' | python3 "$SCRATCH/probe-plugin/hooks/probe.py" post-tool-use >/dev/null)` three times; record the median.

A fact the transcript cannot show (e.g. stream-json omits hook output) is UNOBSERVABLE, not CONFIRMED. Say what you tried.

- [ ] **Step 5: Write `docs/hook-semantics.md`**

Structure:
```markdown
# Hook semantics — observed

Observed <ISO timestamp> with `claude --version` = <output>.
Probe: a throwaway plugin emitting one token per event (code reproduced in
docs/plans/2026-09-14-claimlock/01-hook-semantics-spike.md).

| # | Fact | Verdict | Evidence (command + observed output, verbatim excerpt) |
|---|---|---|---|
| F1 | ... | CONFIRMED | ... |
...

Hook process cost (python3 start + trivial body), median of 3: <ms>.

## Consequences for the design
<one line per REFUTED/UNOBSERVABLE fact: what Task 8 must do differently, or "none">
```

- [ ] **Step 6: Commit**

```bash
git add docs/hook-semantics.md
git commit -m "docs: observed Claude Code hook semantics the design depends on" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014q4pXbksVJoyHtoeuLUwhv"
```

If any of F1–F4 is REFUTED: report DONE_WITH_CONCERNS naming the fact; the controller revises Task 8 before it runs.
