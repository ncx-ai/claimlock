#!/bin/bash
# Re-run one named scenario variant from scenarios.md in a closed-book headless
# session.
#
#   tests/skills/run-scenario.sh <variant> <fixtures-dir> <out-dir> [skill]
#
# <variant> is a heading in scenarios.md (e.g. s5-R3). <fixtures-dir> is the path
# fixtures.sh printed. With [skill], the GREEN preamble and that skill's current
# SKILL.md are prepended to the prompt. Writes <out-dir>/<label>.{jsonl,err,txt}.
set -uo pipefail
variant=$1; fixtures=$2; out=$3; skill=${4:-}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
label=${LABEL:-$variant${skill:+-green}}
mkdir -p "$out"

base=('Bash(claimlock:*)' 'Bash(git:*)' 'Bash(cat:*)' 'Bash(ls:*)' Read Grep Glob)
case ${variant%%-*} in
  s1) fixture=$fixtures/s1; tools=("${base[@]}") ;;
  s2) fixture=$fixtures/s2; tools=("${base[@]}" Edit) ;;
  s3) fixture=$fixtures/s3; tools=("${base[@]}" Edit Write) ;;
  s4) fixture=$fixtures/s4; tools=("${base[@]}" Edit Write) ;;
  s7) fixture=$fixtures/s7; tools=("${base[@]}" Edit Write) ;;
  s5|s6) fixture=none; tools=(Read) ;;
  *) echo "unknown variant $variant" >&2; exit 2 ;;
esac

# A standalone copy of the CLI, so no prompt or PATH entry names this repository.
DIST=$(mktemp -d)
mkdir -p "$DIST/bin" "$DIST/lib"
cp "$REPO/bin/claimlock" "$DIST/bin/"
cp -r "$REPO/lib/claimlock" "$DIST/lib/"
rm -rf "$DIST/lib/claimlock/__pycache__"

prompt=$(python3 - "$REPO/tests/skills/scenarios.md" "$variant" <<'EOF'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().split("\n")
i = lines.index("### " + sys.argv[2])
while not lines[i].startswith("````text"):
    i += 1
j = lines.index("````", i + 1)
sys.stdout.write("\n".join(lines[i + 1:j]))
EOF
) || { echo "variant $variant not found" >&2; exit 2; }
if [ -n "$skill" ]; then
  prompt=$(printf 'You have the following skill available. It applies to this task; follow it.\n\n<skill>\n%s\n</skill>\n\n%s' \
    "$(cat "$REPO/skills/$skill/SKILL.md")" "$prompt")
fi

W=$(mktemp -d)
[ "$fixture" != none ] && cp -a "$fixture"/. "$W"/
cd "$W"
PATH=$DIST/bin:$PATH timeout 300 claude -p "$prompt" --safe-mode --output-format stream-json --verbose \
  --permission-mode acceptEdits --allowedTools "${tools[@]}" > "$out/$label.jsonl" 2> "$out/$label.err"
rc=$?
python3 - "$out/$label.jsonl" > "$out/$label.txt" <<'EOF'
import json, sys
for line in open(sys.argv[1]):
    try: d = json.loads(line)
    except ValueError: continue
    if d.get("type") == "assistant":
        for b in d["message"]["content"]:
            if b.get("type") == "text": print("TEXT:", b["text"])
            elif b.get("type") == "tool_use": print("TOOL:", json.dumps(b["input"]))
    elif d.get("type") == "user" and isinstance(d["message"]["content"], list):
        for b in d["message"]["content"]:
            if b.get("type") == "tool_result":
                r = b.get("content"); print("RESULT:", (r if isinstance(r, str) else json.dumps(r))[:600])
    elif d.get("type") == "result":
        print("FINAL:", d.get("result"))
EOF
if [ "$fixture" != none ]; then
  { echo "--- post-run state (workdir $W)"; git -C "$W" status --short; PATH=$DIST/bin:$PATH claimlock -C "$W" check; } >> "$out/$label.txt" 2>&1
fi
echo "$label exit=$rc"
