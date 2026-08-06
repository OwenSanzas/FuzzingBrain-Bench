#!/usr/bin/env python3
"""Roll a sweep's crash signatures up into the BUGS they point at.

    PYTHONPATH=. python3 tools/gen_bug_clusters.py run-opus

Companion to gen_crash_signatures.py. That script answers "which crashes did this
run find"; this one answers "how many distinct DEFECTS were those", by grouping
each challenge's signatures with fbbench.grading.bugs -- the same rules the report
and the leaderboard use, so the three can never disagree.

Reads output/<run>/*/*/seed-0/score.json -- the seed-0 dirs ONLY, so parked
`seed-0.errored-N` / `seed-0.killed-N` copies never leak into the roll-up -- and
writes output/<run>/bug_clusters.json.

The written file explains its own fields (`legend`) and is ordered for reading:
the challenges with the most crash paths come first, challenges that never
crashed are a plain list of names at the end. Nothing is repeated twice.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))

from fbbench.grading.bugs import cluster  # noqa: E402

# Why each rule in fbbench.grading.bugs merged what it merged, in words a reader
# of the JSON can act on. Keyed by Bug.rule.
WHY = {
    "faulting-frame":
        "same faulting function: these crashes happened in the same code, "
        "reached from different callers",
    "recursion-cycle":
        "one runaway recursion: a blown stack dies at an arbitrary point in the "
        "loop, so every signature from it is the same defect",
    "unbounded-allocation":
        "one unchecked size: an out-of-memory surfaces at whichever allocation "
        "first crossed the limit, so the place it died is not the defect",
    "same-file":
        "same faulting file: one defect spread over several functions of one file",
    "no-frames":
        "no usable stack trace, so this crash cannot be attributed to any code; "
        "kept separate rather than guessed into another bug",
}

LEGEND = {
    "crash_path":
        "One crash signature: a fault type plus the top 3 function names from the "
        "stack trace. Reaching the same defect from two different callers gives "
        "two crash paths.",
    "bug":
        "One defect. Several crash paths that fault in the same code are ONE bug: "
        "reaching it four ways is a real skill, but there is still one thing to fix.",
    "signature":
        "fault-type | faulting function | its caller | its caller's caller. The "
        "fault type is deliberately NOT used to tell bugs apart: the same "
        "out-of-bounds read shows up as heap-buffer-overflow or segv depending on "
        "how far past the end it landed.",
    "faulted_in":
        "The function(s) these crashes faulted in, after skipping functions that "
        "allocate or copy on someone else's behalf (g_malloc0, memcpy, "
        "*_allocator): a bad size passed to an allocator is a defect in the "
        "caller, not in the allocator. More than one entry means the rule below "
        "merged across sites. EMPTY means the crash printed no stack we could "
        "attribute to any code (a timeout, or only runtime functions), so it "
        "counts as its own bug and is never merged into another.",
    "one_bug_because":
        "Why these crash paths are one defect.",
    "location_detail":
        "'function + file + line' when score.json recorded crash_frames, so bugs "
        "could also be merged by faulting FILE. 'function names only' for runs "
        "made before frames were recorded: grouping still works, it just merges "
        "less, so the bug count there is an upper bound.",
}


def build(run: str) -> dict:
    root = BENCH / "output" / run
    scores = sorted(root.glob("*/*/seed-0/score.json"))
    if not scores:
        raise SystemExit(f"no seed-0/score.json under {root}")

    crashed: list[dict] = []
    clean: list[str] = []
    models: set[str] = set()
    for path in scores:
        score = json.loads(path.read_text())
        challenge = score["bug_id"]
        models.add(score.get("model", ""))
        sigs = sorted(score.get("crash_signatures") or [])
        if not sigs:
            clean.append(challenge)
            continue
        frames = score.get("crash_frames") or {}
        has_loc = any(frames.get(s) for s in sigs)
        groups = cluster({s: frames.get(s) or [] for s in sigs})

        crashed.append({
            "challenge": challenge,
            "crash_paths": len(sigs),
            "bugs": len(groups),
            "location_detail": ("function + file + line" if has_loc
                                else "function names only"),
            # One entry per bug, deliberately shallow: the signature string
            # already contains the fault type and the call path, so splitting it
            # into extra fields printed the same text three times and was most of
            # what made this file unreadable.
            "bugs_found": [
                {
                    "crash_paths": g.paths,
                    "faulted_in": g.sites,
                    "one_bug_because": WHY.get(g.rule, g.rule),
                    "signatures": g.signatures,
                    **({"file": g.files[0]} if g.files else {}),
                }
                for g in groups
            ],
        })

    # Most crash paths first: the challenges where grouping actually did something
    # are the ones worth reading, and 30-odd empty entries at the top were what
    # made the previous version unreadable.
    crashed.sort(key=lambda c: (-c["crash_paths"], -c["bugs"], c["challenge"]))
    # One value for the whole run when every challenge agrees, which is the normal
    # case: frames are recorded per RUN, not per challenge. Stated once here rather
    # than as a list of names, since each challenge already carries its own.
    detail = {c["location_detail"] for c in crashed}
    return {
        "run": run,
        "model": sorted(models)[0] if len(models) == 1 else sorted(models),
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": {
            "challenges": len(crashed) + len(clean),
            "challenges_that_crashed": len(crashed),
            # The two numbers side by side, which is the whole point of the file.
            "crash_paths": sum(c["crash_paths"] for c in crashed),
            "bugs": sum(c["bugs"] for c in crashed),
            "location_detail": (detail.pop() if len(detail) == 1
                                else "mixed" if detail else "n/a"),
        },
        "legend": LEGEND,
        "challenges": crashed,
        "challenges_that_never_crashed": sorted(clean),
    }


if __name__ == "__main__":
    run = sys.argv[1] if len(sys.argv) > 1 else "run-opus"
    doc = build(run)
    out = BENCH / "output" / run / "bug_clusters.json"
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    t = doc["totals"]
    print(f"wrote {out}")
    print(f"  {t['crash_paths']} crash paths -> {t['bugs']} distinct bugs "
          f"over {t['challenges_that_crashed']} of {t['challenges']} challenges")
    print(f"  grouped by: {t['location_detail']}")
