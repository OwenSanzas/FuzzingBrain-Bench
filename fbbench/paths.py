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


def resolve_output(value: str | None) -> Path:
    """Resolve a --output value to a results-root directory.

    One knob controls where results land (there is no separate namespace flag):

      - not given            -> the default ``output/`` root (results accumulate
                                there and re-runs resume)
      - a bare name          -> nested under the default root, e.g.
                                ``paper-v1`` -> ``output/paper-v1`` (a named campaign)
      - a path               -> used as-is; a value is treated as a path when it
                                contains a separator, is absolute, is ``.``/``..``,
                                or starts with ``~`` (e.g. ``/data/x``, ``./x``,
                                ``output/paper-v1``)

    So ``paper-v1`` and ``output/paper-v1`` resolve to the same place, and a bare
    name never accidentally lands in the cwd.
    """
    default = REPO / "output"
    if not value:
        return default
    if ("/" in value or os.path.isabs(value)
            or value in (".", "..") or value.startswith("~")):
        return Path(value).expanduser()
    return default / value
