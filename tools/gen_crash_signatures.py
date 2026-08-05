#!/usr/bin/env python3
"""Roll a sweep's per-cell crash signatures up into one crash_signatures.json.

    python3 tools/gen_crash_signatures.py run-opus

Reads output/<run>/*/*/seed-0/score.json — the seed-0 dirs ONLY, so parked
`seed-0.errored-N` / `seed-0.killed-N` copies never leak into the roll-up — and
writes output/<run>/crash_signatures.json.

`type` and `frames` are split back out of the signature string rather than
re-derived from the harness output, so this file can never disagree with the
score.json rows it summarises. The split honours signature.py's backslash
escape, because "|" is legal inside a C++ function name (`operator|`).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
SIG_SEP = "|"


def split_sig(sig: str) -> list[str]:
    """Undo SIG_SEP.join(_escape(p) for p in parts)."""
    parts, cur, esc = [], [], False
    for ch in sig:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == SIG_SEP:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def build(run: str) -> dict:
    root = BENCH / "output" / run
    scores = sorted(root.glob("*/*/seed-0/score.json"))
    if not scores:
        raise SystemExit(f"no seed-0/score.json under {root}")

    by_bug: dict[str, list[str]] = {}
    rows: list[dict] = []
    models: set[str] = set()
    for p in scores:
        d = json.loads(p.read_text())
        bug = d["bug_id"]
        models.add(d.get("model", ""))
        sigs = sorted(d.get("crash_signatures") or [])
        by_bug[bug] = sigs
        for s in sigs:
            parts = split_sig(s)
            rows.append({"bug": bug, "signature": s,
                         "type": parts[0], "frames": parts[1:]})

    by_bug = {k: by_bug[k] for k in sorted(by_bug)}
    rows.sort(key=lambda r: (r["bug"], r["signature"]))
    return {
        "run": run,
        "model": sorted(models)[0] if len(models) == 1 else sorted(models),
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": {
            "challenges": len(by_bug),
            "with_crashes": sum(1 for v in by_bug.values() if v),
            "signatures": len(rows),
        },
        "by_bug": by_bug,
        "signatures": rows,
    }


if __name__ == "__main__":
    run = sys.argv[1] if len(sys.argv) > 1 else "run-opus"
    doc = build(run)
    out = BENCH / "output" / run / "crash_signatures.json"
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {out}")
    print(f"  {doc['totals']}")
