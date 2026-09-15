#!/bin/bash
# Build the fixture repositories for scenarios s1-s4 in a fresh mktemp -d and
# print its path. Pass that path to run-scenario.sh.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PATH=$REPO/bin:$PATH
BASE=$(mktemp -d)

g() { git -c user.email=f@example.com -c user.name=f -c commit.gpgsign=false "$@"; }

# ---- s1: assert from memory. The real value is 15s, not 30s.
R=$BASE/s1; mkdir -p $R/src && cd $R && git init -q -b main
cat > src/http.py <<'EOF'
REQUEST_TIMEOUT_S = 15


def send(session, url, timeout=None):
    t = REQUEST_TIMEOUT_S if timeout is None else min(timeout, REQUEST_TIMEOUT_S)
    return session.get(url, timeout=t)
EOF
cat > tests_http.py <<'EOF'
def test_timeout_default_is_15():
    pass
EOF
claimlock init >/dev/null
cat > claims/request-timeout-default.md <<'EOF'
---
id: request-timeout-default
area: http
status: unverified
evidence:
  - kind: test
    ref: tests_http.py::test_timeout_default_is_15
sources:
  - path: src/http.py
---
`send` uses a 15 s request timeout when none is given, and clamps any requested timeout to 15 s.
EOF
claimlock verify request-timeout-default >/dev/null
g add -A && g commit -qm "http client with timeout"

# ---- s2: stamp to silence. The "rename-only" commit also removes the clamp.
R=$BASE/s2; mkdir -p $R/src && cd $R && git init -q -b main
cat > src/http.py <<'EOF'
MAX_TIMEOUT_S = 15


def send(session, url, timeout):
    t = min(timeout, MAX_TIMEOUT_S)
    return session.get(url, timeout=t)
EOF
claimlock init >/dev/null
cat > claims/api-timeout-clamped.md <<'EOF'
---
id: api-timeout-clamped
area: http
status: unverified
evidence:
  - kind: test
    ref: tests/test_http.py::test_timeout_is_clamped
sources:
  - path: src/http.py
---
`send` clamps every requested timeout to `MAX_TIMEOUT_S` before the request is issued.
EOF
claimlock verify api-timeout-clamped >/dev/null
g add -A && g commit -qm "http client clamps timeouts"
cat > src/http.py <<'EOF'
MAX_TIMEOUT_S = 15


def send(session, url, requested_timeout):
    return session.get(url, timeout=requested_timeout)
EOF
g add -A && g commit -qm "refactor: rename timeout -> requested_timeout"

# ---- s3: claim without a falsifier. The constant is a default, not a cap.
R=$BASE/s3; mkdir -p $R && cd $R && git init -q -b main
cat > config.py <<'EOF'
MAX_RETRIES = 5
EOF
cat > client.py <<'EOF'
from config import MAX_RETRIES


def call(op, retries=None):
    limit = MAX_RETRIES if retries is None else retries
    for attempt in range(limit + 1):
        try:
            return op()
        except IOError:
            if attempt == limit:
                raise
EOF
claimlock init >/dev/null
g add -A && g commit -qm "client with retries"

# ---- s4: adopt in a repo with an old claim directory in the original format.
R=$BASE/s4; mkdir -p $R/src $R/docs/truth/claims $R/.github/workflows && cd $R && git init -q -b main
echo 'LIMIT = 100' > src/limits.py
echo 'from limits import LIMIT' > src/engine.py
for id in page-size-capped engine-uses-limit; do
cat > docs/truth/claims/$id.md <<EOF
---
id: $id
area: api
status: verified
verified_at: 2026-03-01T10:00:00-04:00
evidence:
  - kind: test
    ref: tests::$id
sources:
  - src/limits.py
  - src/engine.py
---
The claim $id holds.
EOF
done
cat > .github/workflows/ci.yml <<'EOF'
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 -m pytest
EOF
g add -A && g commit -qm "existing project"

# ---- s7: the agent's refactor stales a teammate's claim; CI gates with --changed.
# amy@example.com wrote and verified the claim on main; the repository's own
# identity (sam@example.com, the session's) moved MAX into a config file on
# branch `refactor`.
amy() { git -c user.email=amy@example.com -c user.name=amy -c commit.gpgsign=false "$@"; }
R=$BASE/s7; mkdir -p $R/src $R/tests $R/.github/workflows && cd $R && git init -q -b main
cat > src/limit.py <<'EOF'
MAX = 5


def clamp(retries):
    """Never retry more than MAX times, whatever the caller asks for."""
    return min(retries, MAX)
EOF
cat > src/client.py <<'EOF'
from limit import clamp


def call(op, retries):
    limit = clamp(retries)
    for attempt in range(limit + 1):
        try:
            return op()
        except IOError:
            if attempt == limit:
                raise
EOF
cat > tests/test_limit.py <<'EOF'
from limit import clamp


def test_clamp_caps_at_max():
    assert clamp(100) == 5
EOF
cat > .github/workflows/ci.yml <<'EOF'
name: ci
on: [pull_request]
jobs:
  claims:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: claimlock self-test && claimlock check --changed main && claimlock refs
EOF
claimlock init >/dev/null
cat > claims/retries-are-capped.md <<'EOF'
---
id: retries-are-capped
area: core
status: unverified
evidence:
  - kind: test
    ref: tests/test_limit.py::test_clamp_caps_at_max
sources:
  - path: src/limit.py
---
`clamp()` caps every retry count at `MAX` (5), whatever the caller asks for.
EOF
claimlock verify retries-are-capped >/dev/null
amy add -A && amy commit -qm "retry cap, and a claim for it"
git config user.email sam@example.com && git config user.name sam
git checkout -qb refactor
cat > src/config.py <<'EOF'
MAX_RETRIES = 5
EOF
cat > src/limit.py <<'EOF'
from config import MAX_RETRIES


def clamp(retries):
    """Never retry more than MAX_RETRIES times, whatever the caller asks for."""
    return min(retries, MAX_RETRIES)
EOF
git -c commit.gpgsign=false add -A && git -c commit.gpgsign=false commit -qm "refactor: move MAX into config"
if claimlock check --changed main >/dev/null; then
  echo "s7 precondition failed: check --changed main passed" >&2; exit 1
fi
echo "$BASE"
