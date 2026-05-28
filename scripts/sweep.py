#!/usr/bin/env python3
"""Batch orchestrator for FuzzingBrain Bench.

Runs a (models x bugs x samples) matrix through `python -m runner`, one
episode per subprocess (isolated + per-episode timeout), resumable (skips
cells whose score.json already exists), with a live cost tally and a final
leaderboard. Each cell lands at runs/<bug>/<model>/seed-N/ where N is the
sample index (kept named `seed-N` for back-compat with the legacy 518-row
dataset; the runner itself has no --seed arg).

Examples:
  # cost probe: opus on 5 bugs, 1 sample
  python scripts/sweep.py --models claude-opus-4-7 \\
      --bugs mongoose-mg-match-overflow,netsnmp-vacm-parse-npd,jsonjava-jsonml-classcast,simdutf-utf16-utf8-overflow,openldap-parse-whsp

  # full sweep, default lineup, 2 samples per (model, bug) for best-of-2 union
  python scripts/sweep.py --models sweep --bugs all --samples 0,1

  # keep every graded blob (bucketed by solved/failed)
  python scripts/sweep.py --models sweep --bugs all --samples 0 --preserve-pocs

  # just re-aggregate the leaderboard from existing runs/
  python scripts/sweep.py --report-only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER = [sys.executable, "-m", "runner"]
sys.path.insert(0, str(REPO / "runner"))


def discover_bugs() -> list[str]:
    return sorted(p.name for p in (REPO / "bugs").glob("*/*") if (p / "bench.yaml").is_file())


def resolve_models(spec: str) -> list[str]:
    from registry import default_sweep, SUPPORTED_MODELS
    if spec == "sweep":
        return default_sweep()
    if spec == "all":
        return SUPPORTED_MODELS
    return [m.strip() for m in spec.split(",") if m.strip()]


def resolve_bugs(spec: str) -> list[str]:
    allbugs = discover_bugs()
    if spec == "all":
        return allbugs
    want = [b.strip() for b in spec.split(",") if b.strip()]
    unknown = [b for b in want if b not in allbugs]
    if unknown:
        sys.exit(f"unknown bug(s): {', '.join(unknown)}")
    return want


def cell_dir(out: Path, bug: str, model: str, sample: int) -> Path:
    """Per-cell output dir. `sample` indexes repeat runs of (bug, model).

    Keeps the legacy `seed-N` directory naming for back-compat with the
    518 existing data points; the integer no longer drives sampling
    (runner has no --seed arg) — it is purely a directory label."""
    return out / bug / model / f"seed-{sample}"


def bug_kb(bug: str) -> list[str]:
    """The capability_set (required flags) for a bug, from its bench.yaml."""
    import re
    for p in (REPO / "bugs").glob(f"*/{bug}/bench.yaml"):
        m = re.search(r"capability_set\s*:\s*\[(.*?)\]", p.read_text(), re.S)
        if m:
            return [x.strip() for x in m.group(1).split(",") if x.strip()]
    return ["reach", "crash", "class", "site"]


def run_cell(model: str, bug: str, sample: int, max_turns: int, out: Path,
             timeout: int, preserve_pocs: bool = False) -> dict | None:
    cd = cell_dir(out, bug, model, sample)
    cmd = RUNNER + ["--bug", bug, "--model", model,
                    "--max-turns", str(max_turns),
                    "--out-dir", str(cd)]
    if preserve_pocs:
        cmd.append("--preserve-pocs")
    try:
        subprocess.run(cmd, cwd=REPO, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    sj = cd / "score.json"
    return json.loads(sj.read_text()) if sj.is_file() else {"error": "no score.json"}


def aggregate(out: Path, models: list[str], bugs: list[str], seeds: list[int]) -> None:
    print("\n" + "=" * 78)
    print(f"  {'model':24s} {'solved':>7s} {'reach':>6s} {'crash':>6s} "
          f"{'class':>6s} {'site':>6s} {'refus':>6s} {'cost$':>8s}")
    print("  " + "-" * 74)
    for model in models:
        agg = {"reach": 0, "crash": 0, "class": 0, "site": 0}
        solved = refusals = n = 0
        cost = 0.0
        for bug in bugs:
            # best-of-seeds union per cell
            caps = {"reach": False, "crash": False, "class": False, "site": False}
            seen = False
            for seed in seeds:
                sj = cell_dir(out, bug, model, seed) / "score.json"
                if not sj.is_file():
                    continue
                seen = True
                s = json.loads(sj.read_text())
                for k in caps:
                    if s.get("capabilities", {}).get(k) == "fired":
                        caps[k] = True
                if s.get("terminated_reason") == "refusal":
                    refusals += 1
                if s.get("total_usd"):
                    cost += s["total_usd"]
            if not seen:
                continue
            n += 1
            for k in agg:
                agg[k] += int(caps[k])
            # solved = every flag in the bug's K_b fired (per bench.yaml).
            if all(caps[k] for k in bug_kb(bug)):
                solved += 1
        print(f"  {model:24s} {f'{solved}/{n}':>7s} {agg['reach']:>6d} "
              f"{agg['crash']:>6d} {agg['class']:>6d} {agg['site']:>6d} "
              f"{refusals:>6d} {cost:>8.2f}")
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description="FuzzingBrain Bench batch sweep")
    ap.add_argument("--models", default="claude-opus-4-7",
                    help="'sweep' | 'all' | comma list of model ids")
    ap.add_argument("--bugs", default="all", help="'all' | comma list of bug ids")
    ap.add_argument("--samples", "--seeds", dest="samples", default="0",
                    help="comma list of repeat indices, e.g. 0,1,2 — each sample is one independent run")
    ap.add_argument("--preserve-pocs", action="store_true",
                    help="forward --preserve-pocs to runner (save every graded blob)")
    ap.add_argument("--max-turns", type=int, default=300,
                    help="turn budget per episode (default 300, matches ExploitBench)")
    ap.add_argument("--timeout", type=int, default=1800, help="per-episode seconds")
    ap.add_argument("--exp", "-e", default=None,
                    help="experiment namespace (default: auto-assigned exp-<timestamp>). "
                         "Pass an existing name (e.g. paper-v1) to resume that campaign.")
    ap.add_argument("--output", default=str(REPO / "runs"),
                    help="runs root (default: ./runs). Cells land at <output>/<exp>/<bug>/<model>/seed-N/.")
    ap.add_argument("--report-only", action="store_true",
                    help="skip running; just re-aggregate from <output>/<exp>/")
    args = ap.parse_args()

    if args.exp:
        exp = args.exp
    else:
        import datetime
        exp = "exp-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        print(f"  no --exp given; auto-assigned: {exp}")
    out = Path(args.output) / exp
    models = resolve_models(args.models)
    bugs = resolve_bugs(args.bugs)
    samples = [int(s) for s in args.samples.split(",") if s.strip() != ""]

    if args.report_only:
        aggregate(out, models, bugs, samples)
        return 0

    cells = [(m, b, s) for m in models for b in bugs for s in samples]
    done = sum(1 for m, b, s in cells if (cell_dir(out, b, m, s) / "score.json").is_file())
    print(f"  sweep: {len(models)} models x {len(bugs)} bugs x {len(samples)} samples "
          f"= {len(cells)} cells ({done} already done, {len(cells)-done} to run)")

    total_cost = 0.0
    t0 = time.time()
    for i, (model, bug, sample) in enumerate(cells, 1):
        if (cell_dir(out, bug, model, sample) / "score.json").is_file():
            continue
        tag = f"[{i}/{len(cells)}] {model} / {bug} / sample-{sample}"
        print(f"  {tag} ...", flush=True)
        r = run_cell(model, bug, sample, args.max_turns, out, args.timeout,
                     preserve_pocs=args.preserve_pocs)
        if r and "error" not in r:
            c = r.get("total_usd") or 0.0
            total_cost += c
            ts = r.get("tier_score", "?")
            print(f"      -> {ts}/4  {r.get('terminated_reason','')}  "
                  f"${c:.4f}  (running ${total_cost:.2f})", flush=True)
        else:
            print(f"      -> FAILED: {r.get('error') if r else 'unknown'}", flush=True)

    print(f"\n  done in {time.time()-t0:.0f}s, spent ~${total_cost:.2f} this run")
    aggregate(out, models, bugs, samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
