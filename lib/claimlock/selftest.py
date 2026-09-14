"""Prove the detectors can fire.

A gate nobody has watched fail is decoration. This builds a throwaway store —
in a plain directory and, when git is available, in a repository — pins a
source, then edits it, deletes it, and plants a dangling marker, requiring
each detector to report. In the git arm it also proves a freshly verified but
unstaged source reads `unanchored` until it is staged. It exercises the real
code paths, not the store in the current project.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import claims as C
from . import ops, refs
from . import project as P
from .pins import Hasher

PROBE = """---
id: probe
area: self-test
status: unverified
evidence:
  - kind: run
    ref: claimlock self-test
sources:
  - src.txt
---
The probe source is unchanged.
"""


def run(out=print) -> int:
    results = []

    def expect(label, got, want):
        ok = got == want
        results.append(ok)
        out(f"  {'ok  ' if ok else 'FAIL'}  {label}: expected {want!r}, got {got!r}")

    with tempfile.TemporaryDirectory() as d:
        arms = [("plain directory", False)]
        if shutil.which("git"):
            arms.append(("git repository", True))
        else:
            out("  skip  git arm: git is not on PATH (the plain-directory arm still ran)")
        for label, use_git in arms:
            root = Path(d) / ("git" if use_git else "plain")
            root.mkdir()
            if use_git:
                for args in (["init", "-q"], ["config", "user.email", "self@test"], ["config", "user.name", "self"]):
                    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            (root / P.CONFIG).write_text("")
            (root / "claims").mkdir()
            (root / "src.txt").write_text("before\n")
            (root / "claims" / "probe.md").write_text(PROBE)
            project = P.load(root)
            ops.verify(project, "probe")

            def state():
                c = next(x for x in C.load_claims(project) if x.id == "probe")
                return C.freshness(c, project, Hasher(root, None),
                                   C.anchors_for(project, c.sources))[0]

            if use_git:
                expect(f"[{label}] verified, unstaged source", state(), "unanchored")
                subprocess.run(["git", "add", "src.txt"], cwd=root, check=True, capture_output=True)
            expect(f"[{label}] pinned source", state(), "fresh")
            (root / "src.txt").write_text("after, and a different length\n")
            expect(f"[{label}] edited source", state(), "stale")
            (root / "src.txt").unlink()
            expect(f"[{label}] deleted source", state(), "missing")
            (root / "doc.md").write_text("Claim: `no-such-claim`\nClaim: `probe`\n")
            markers, _ = refs.scan(project)
            ids = {c.id for c in C.load_claims(project)}
            expect(f"[{label}] dangling markers", sorted(m.id for m in markers if m.id not in ids),
                   ["no-such-claim"])

    out("")
    failed = results.count(False)
    if failed:
        out(f"SELF-TEST: {failed} of {len(results)} checks FAILED — every green result from this build is meaningless")
        return 1
    out(f"SELF-TEST: all {len(results)} checks passed")
    return 0
