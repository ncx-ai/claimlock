"""Where a store lives and how it is configured.

The root is anchored by `.claimlock.toml` first, so a store created before a
repository exists does not move when `git init` runs later. Git is only a
fallback for finding the root, never a requirement.
"""
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG = ".claimlock.toml"
DEFAULTS = {
    "claims_dir": "claims",
    "marker_globs": ["**/*.md"],
    "marker_pattern": r"Claim: `([a-z0-9][a-z0-9-]*)`",
}


class ConfigError(Exception):
    pass


@dataclass
class Project:
    root: Path
    claims_dir: Path
    marker_globs: list
    marker_pattern: re.Pattern
    has_config: bool

    @property
    def state_dir(self) -> Path:
        return self.root / ".claimlock"

    def has_store(self) -> bool:
        return self.has_config or self.claims_dir.is_dir()


def is_within(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def _git_toplevel(start: Path):
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return Path(out).resolve() if r.returncode == 0 and out else None


def _find_root(start: Path):
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / CONFIG).is_file():
            return d, True
    return (_git_toplevel(start) or start), False


def load(start: Path) -> Project:
    root, has_config = _find_root(Path(start))
    cfg = dict(DEFAULTS)
    if has_config:
        try:
            data = tomllib.loads((root / CONFIG).read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ConfigError(f"{CONFIG}: {e}") from None
        unknown = sorted(set(data) - set(DEFAULTS))
        if unknown:
            raise ConfigError(f"{CONFIG}: unknown key(s) {unknown}; allowed: {sorted(DEFAULTS)}")
        cfg.update(data)
    if not isinstance(cfg["claims_dir"], str) or not cfg["claims_dir"]:
        raise ConfigError(f"{CONFIG}: claims_dir must be a string")
    globs = cfg["marker_globs"]
    if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
        raise ConfigError(f"{CONFIG}: marker_globs must be a list of strings")
    try:
        pattern = re.compile(cfg["marker_pattern"])
    except (re.error, TypeError) as e:
        raise ConfigError(f"{CONFIG}: marker_pattern: {e}") from None
    if pattern.groups < 1:
        raise ConfigError(f"{CONFIG}: marker_pattern needs a capture group for the claim id")
    claims_dir = (root / cfg["claims_dir"]).resolve()
    if not is_within(claims_dir, root):
        raise ConfigError(f"{CONFIG}: claims_dir escapes the project root")
    return Project(root, claims_dir, list(globs), pattern, has_config)


def safe_source(root: Path, rel: str):
    """The file a root-relative POSIX path names, or None if it could escape the root."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return None
    if re.match(r"^[A-Za-z]:", rel) or ".." in rel.split("/"):
        return None
    p = root / rel
    if not is_within(p.resolve(), root.resolve()):
        return None
    return p
