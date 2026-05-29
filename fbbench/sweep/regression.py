#!/usr/bin/env python3
"""Grade every shipped bug's poc.bin through the MCP server.

Pass criterion: every flag in the bug's `capability_set` fires `fired`,
with `agreed: true` across the 3 randomized rounds. Exit 0 iff all PASS.

  python -m fbbench.sweep.regression
"""
from __future__ import annotations

import sys

from fbbench.grading import capability_set, grade_blob, list_bugs
from fbbench.paths import SERVER

FLAGS = ["reach", "crash", "class", "site"]


def main() -> int:
    if not SERVER.exists():
        print(f"error: {SERVER} not built. run `make mcp-server`", file=sys.stderr)
        return 2

    bugs = [(b, d) for b, d in list_bugs() if (d / "poc" / "poc.bin").is_file()]
    if not bugs:
        print("error: no shippable bugs found", file=sys.stderr)
        return 2

    print(f"Grading {len(bugs)} bugs through {SERVER.name}")
    print("=" * 72)

    n_pass = 0
    for bug_id, bd in bugs:
        try:
            r, elapsed = grade_blob(bd, bd / "poc" / "poc.bin", rounds=3, timeout=240)
            caps = r.get("capabilities", {})
            fired = ",".join(c for c in FLAGS if caps.get(c) == "fired")
            ok = all(caps.get(c) == "fired" for c in capability_set(bd)) and r.get("agreed", False)
            status = "PASS" if ok else "FAIL"
        except Exception as e:
            status, fired, elapsed = f"ERR ({e})", "", 0.0
        if status == "PASS":
            n_pass += 1
        print(f"  [{status:4s}] {bug_id:38s} fired=[{fired}] ({elapsed:.0f}s)")

    print("=" * 72)
    print(f"\n  {n_pass}/{len(bugs)} PASS")
    return 0 if n_pass == len(bugs) else 1


if __name__ == "__main__":
    sys.exit(main())
