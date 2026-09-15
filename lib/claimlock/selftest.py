"""Prove the detectors can fire.

A gate nobody has watched fail is decoration. This builds a throwaway store —
in a plain directory and, when git is available, in a repository — pins a
source, then edits it, deletes it, and plants a dangling marker, requiring
each detector to report. A second probe pins a marker-delimited region of the
same file, staying fresh past an edit outside its markers, going stale for an
edit inside them, and reading missing once its end marker is gone. In the git
arm it also proves a freshly verified but unstaged source reads `unanchored`
until it is staged, and — after its other checks — that a committed `git mv`
of a third probe's source reads `renamed`, then `fresh` after `claimlock
follow`. It exercises the real code paths, not the store in the current
project.
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

PROBE_REGION = """---
id: probe-region
area: self-test
status: unverified
evidence:
  - kind: run
    ref: claimlock self-test
sources:
  - path: src.txt
    region: probe
---
The probe region is unchanged.
"""

PROBE_RENAME = """---
id: probe-rename
area: self-test
status: unverified
evidence:
  - kind: run
    ref: claimlock self-test
sources:
  - moved.txt
---
The probe rename source is unchanged.
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
            (root / "src.txt").write_text(
                "before\n# claimlock:begin probe\ninside\n# claimlock:end probe\nafter\n")
            (root / "claims" / "probe.md").write_text(PROBE)
            (root / "claims" / "probe-region.md").write_text(PROBE_REGION)
            project = P.load(root)
            ops.verify(project, "probe")
            ops.verify(project, "probe-region")

            def state(cid):
                c = next(x for x in C.load_claims(project) if x.id == cid)
                return C.freshness(c, project, Hasher(root, None),
                                   C.anchors_for(project, c.sources))[0]

            if use_git:
                expect(f"[{label}] verified, unstaged source", state("probe"), "unanchored")
                subprocess.run(["git", "add", "src.txt"], cwd=root, check=True, capture_output=True)
            expect(f"[{label}] pinned source", state("probe"), "fresh")
            expect(f"[{label}] region: pinned source", state("probe-region"), "fresh")
            (root / "src.txt").write_text(
                "before\n# claimlock:begin probe\ninside\n# claimlock:end probe\nafter-changed\n")
            expect(f"[{label}] region: edit outside the region", state("probe-region"), "fresh")
            (root / "src.txt").write_text(
                "before\n# claimlock:begin probe\ninside-changed\n# claimlock:end probe\nafter-changed\n")
            expect(f"[{label}] region: edit inside the region", state("probe-region"), "stale")
            (root / "src.txt").write_text("before\n# claimlock:begin probe\ninside-changed\nafter-changed\n")
            expect(f"[{label}] region: end marker removed", state("probe-region"), "missing")
            (root / "src.txt").write_text("after, and a different length\n")
            expect(f"[{label}] edited source", state("probe"), "stale")
            (root / "src.txt").unlink()
            expect(f"[{label}] deleted source", state("probe"), "missing")
            (root / "doc.md").write_text("Claim: `no-such-claim`\nClaim: `probe`\n")
            markers, _ = refs.scan(project)
            ids = {c.id for c in C.load_claims(project)}
            expect(f"[{label}] dangling markers", sorted(m.id for m in markers if m.id not in ids),
                   ["no-such-claim"])

            if use_git:
                (root / "moved.txt").write_text("moved content\n")
                (root / "claims" / "probe-rename.md").write_text(PROBE_RENAME)
                ops.verify(project, "probe-rename")
                subprocess.run(["git", "add", "moved.txt", "claims/probe-rename.md"],
                               cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "commit", "-q", "-m", "add probe-rename"],
                               cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "mv", "moved.txt", "moved2.txt"],
                               cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "commit", "-q", "-m", "rename probe-rename"],
                               cwd=root, check=True, capture_output=True)

                def rename_state():
                    c = next(x for x in C.load_claims(project) if x.id == "probe-rename")
                    renames = C.renames_for(project, c.sources)
                    return C.freshness(c, project, Hasher(root, None),
                                       C.anchors_for(project, c.sources), renames=renames)[0]

                expect(f"[{label}] renamed source", rename_state(), "renamed")
                moves = ops.follow(project, "probe-rename")
                expect(f"[{label}] followed source", moves[0][2] if moves else None, "fresh")

    out("")
    failed = results.count(False)
    if failed:
        out(f"SELF-TEST: {failed} of {len(results)} checks FAILED — every green result from this build is meaningless")
        return 1
    out(f"SELF-TEST: all {len(results)} checks passed")
    return 0
