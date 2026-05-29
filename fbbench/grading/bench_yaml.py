"""bench.yaml reading + bug discovery, with no external YAML dependency.

bench.yaml top-levels we need (title, project, capability_set, ...) are flat
scalars or one-line [lists], so a tiny ad-hoc reader avoids pulling PyYAML
into the stdlib-only CLI path. This replaces the four near-identical readers
that used to live in fb-bench, the runner, and the sweep scripts.
"""
from __future__ import annotations

from pathlib import Path

from fbbench.paths import REPO

DEFAULT_KB = ["reach", "crash", "class", "site"]


def read_bench(path: Path) -> dict:
    """Parse a bench.yaml's top-level scalars and one-line [lists]."""
    out: dict = {}
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].rstrip()
        if not line or ":" not in line or line[0] in " \t-":
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k.strip()] = [s.strip().strip("\"'") for s in v[1:-1].split(",") if s.strip()]
        else:
            out[k.strip()] = v.strip("\"'")
    return out


def capability_set(bug_dir: Path) -> list[str]:
    """K_b (required flags) for a bug, or the full default ladder if unset."""
    kb = read_bench(Path(bug_dir) / "bench.yaml").get("capability_set")
    return kb if isinstance(kb, list) and kb else list(DEFAULT_KB)


def find_bug(bug_id: str, repo: Path = REPO) -> Path | None:
    """Absolute path to bugs/*/<bug_id>, or None if there is no such bug."""
    for proj in (repo / "bugs").iterdir():
        if proj.is_dir() and (proj / bug_id).is_dir():
            return proj / bug_id
    return None


def list_bugs(repo: Path = REPO) -> list[tuple[str, Path]]:
    """Every shippable bug: (bug_id, dir) with both bench.yaml and binaries/."""
    bugs: list[tuple[str, Path]] = []
    for proj in sorted((repo / "bugs").iterdir()):
        if not proj.is_dir():
            continue
        for sub in sorted(proj.iterdir()):
            if (sub / "bench.yaml").is_file() and (sub / "binaries").is_dir():
                bugs.append((sub.name, sub))
    return bugs
