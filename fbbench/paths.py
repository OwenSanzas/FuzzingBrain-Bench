"""Repo-root discovery, shared across the package.

The benchmark's runnable assets (bugs/, tools/, bin/) live in the cloned
repository, not in the installed package. We locate that root once: an
explicit FBBENCH_REPO override wins, else we walk up from the cwd and from
this file looking for the bugs/ + tools/mcp-server/ markers.
"""
from __future__ import annotations

import os
from pathlib import Path


def find_repo_root() -> Path:
    override = os.environ.get("FBBENCH_REPO")
    if override:
        return Path(override).resolve()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for p in (start, *start.parents):
            if (p / "bugs").is_dir() and (p / "tools" / "mcp-server").is_dir():
                return p
    return Path.cwd()


REPO = find_repo_root()
SERVER = REPO / "bin" / "mcp-server"
