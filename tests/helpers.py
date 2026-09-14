"""Shared test fixtures. Every test gets a fresh temp directory."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin" / "claimlock"
sys.path.insert(0, str(REPO / "lib"))


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def make_repo(root: Path, use_git: bool, config: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if use_git:
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "t")
        git(root, "config", "commit.gpgsign", "false")
    (root / ".claimlock.toml").write_text(config)
    (root / "claims").mkdir()
    return root


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def claim_text(cid, status="unverified", evidence=(("test", "suite::case"),), sources=(),
               area="core", body="The thing holds.", extra_lines=()):
    lines = ["---", f"id: {cid}", f"area: {area}", f"status: {status}"]
    lines.append("evidence:" if evidence else "evidence: []")
    for kind, ref in evidence:
        lines += [f"  - kind: {kind}", f"    ref: {ref}"]
    lines.append("sources:" if sources else "sources: []")
    for s in sources:
        lines.append(f"  - path: {s}")
    lines += list(extra_lines)
    lines += ["---", body, ""]
    return "\n".join(lines)


def pinned_text(cid, pins, status="verified"):
    lines = ["---", f"id: {cid}", "area: core", f"status: {status}",
             "evidence:", "  - kind: test", "    ref: s::c", "sources:"]
    for path, blob in pins:
        lines.append(f"  - path: {path}")
        if blob:
            lines.append(f"    blob: {blob}")
    return "\n".join(lines + ["---", "Holds.", ""])


def run_cli(cwd, *args, stdin=None, env=None):
    e = dict(os.environ)
    e["NO_COLOR"] = "1"
    e.update(env or {})
    r = subprocess.run([sys.executable, str(BIN), *args], cwd=cwd, capture_output=True,
                       text=True, input=stdin, env=e, timeout=120)
    return r.returncode, r.stdout, r.stderr


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()

    def tearDown(self):
        self._td.cleanup()
