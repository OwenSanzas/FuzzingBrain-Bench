#!/usr/bin/env python3
"""Batch orchestrator for FuzzingBrain Bench.

Runs a (models x bugs x samples) matrix through `python -m fbbench.runner`, one
episode per subprocess (isolated + per-episode timeout), resumable (skips
cells whose score.json already exists), with a live cost tally and a final
leaderboard. Each cell lands at output/<bug>/<model>/seed-N/ where N is the
sample index (kept named `seed-N` for back-compat with the legacy 518-row
dataset; the runner itself has no --seed arg).

Examples:
  # cost probe: opus on 5 bugs, 1 sample
  python -m fbbench.sweep.orchestrator --models claude-opus-4-7 \\
      --bugs mongoose-01,net-snmp-02,json-java-01,simdutf-01,openldap-02

  # full sweep, default lineup, 2 samples per (model, bug) for best-of-2 union
  python -m fbbench.sweep.orchestrator --models sweep --bugs all --samples 2

  # graded blobs (bucketed solved/failed) are kept by default; opt out with --no-preserve-pocs
  python -m fbbench.sweep.orchestrator --models sweep --bugs all --no-preserve-pocs

  # just re-aggregate the leaderboard from existing output/
  python -m fbbench.sweep.orchestrator --report-only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from fbbench.grading import DEFAULT_KB, capability_set, find_bug, list_bugs
from fbbench.models import SUPPORTED_MODELS, default_sweep
from fbbench.paths import REPO, resolve_output

RUNNER = [sys.executable, "-m", "fbbench.runner"]


def discover_bugs() -> list[str]:
    return [name for name, _ in list_bugs()]


def resolve_models(spec: str) -> list[str]:
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


def _seed_solved(s: dict) -> bool:
    """Authoritative per-seed solve: a single candidate reproduced the full
    target defect (score.solved). Falls back to this seed's own caps for older
    runs that predate the field. NEVER a union across seeds or candidates."""
    if "solved" in s:
        return bool(s["solved"])
    caps = s.get("capabilities", {})
    applicable = {k: v for k, v in caps.items() if v != "n/a"}
    return bool(applicable) and all(v == "fired" for v in applicable.values())


def bug_kb(bug: str) -> list[str]:
    """The capability_set (required flags) for a bug, from its bench.yaml."""
    bd = find_bug(bug)
    return capability_set(bd) if bd else list(DEFAULT_KB)


def run_cell(model: str, bug: str, sample: int, max_turns: int, out: Path,
             timeout: int, preserve_pocs: bool = True,
             full_scan: bool = False) -> dict | None:
    cd = cell_dir(out, bug, model, sample)
    cmd = RUNNER + ["--bug", bug, "--model", model,
                    "--max-turns", str(max_turns),
                    "--out-dir", str(cd)]
    cmd.append("--preserve-pocs" if preserve_pocs else "--no-preserve-pocs")
    if full_scan:
        cmd.append("--full-scan")
    try:
        subprocess.run(cmd, cwd=REPO, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    sj = cd / "score.json"
    return json.loads(sj.read_text()) if sj.is_file() else {"error": "no score.json"}


def aggregate(out: Path, models: list[str], bugs: list[str], seeds: list[int]) -> None:
    print("\n" + "=" * 78)
    print(f"  {'model':24s} {'solved':>7s} {'reach':>6s} {'crash':>6s} {'diff':>7s} "
          f"{'class':>6s} {'site':>6s} {'refus':>6s} {'cost$':>8s}")
    print("  " + "-" * 82)
    for model in models:
        agg = {"reach": 0, "crash": 0, "differential": 0, "class": 0, "site": 0}
        solved = refusals = n = 0
        cost = 0.0
        for bug in bugs:
            # Coverage columns are best-of-seeds per rung (did the model ever
            # reach/crash/... on this bug). Solved is NOT a union: it is whether
            # some SINGLE seed authoritatively solved (score.solved) — a union of
            # rungs across seeds would fake a solve no single attempt achieved.
            caps = {"reach": False, "crash": False, "differential": False, "class": False, "site": False}
            seen = False
            bug_solved = False
            for seed in seeds:
                sj = cell_dir(out, bug, model, seed) / "score.json"
                if not sj.is_file():
                    continue
                seen = True
                s = json.loads(sj.read_text())
                for k in caps:
                    if s.get("capabilities", {}).get(k) == "fired":
                        caps[k] = True
                bug_solved = bug_solved or _seed_solved(s)
                if s.get("terminated_reason") == "refusal":
                    refusals += 1
                if s.get("total_usd"):
                    cost += s["total_usd"]
            if not seen:
                continue
            n += 1
            for k in agg:
                agg[k] += int(caps[k])
            if bug_solved:
                solved += 1
        print(f"  {model:24s} {f'{solved}/{n}':>7s} {agg['reach']:>6d} "
              f"{agg['crash']:>6d} {agg['differential']:>7d} {agg['class']:>6d} {agg['site']:>6d} "
              f"{refusals:>6d} {cost:>8.2f}")
    print("=" * 82)


def main() -> int:
    ap = argparse.ArgumentParser(description="FuzzingBrain Bench batch sweep")
    ap.add_argument("--models", default="claude-opus-4-7",
                    help="'sweep' | 'all' | comma list of model ids")
    ap.add_argument("--bugs", default="all", help="'all' | comma list of bug ids")
    ap.add_argument("--samples", type=int, default=1, metavar="N",
                    help="repeat count: run each (model, bug) N times, stored as "
                         "seed-0..seed-(N-1) (default 1). Resumable: re-run with a "
                         "larger N to add the missing repeats.")
    ap.add_argument("--preserve-pocs", action=argparse.BooleanOptionalAction, default=True,
                    help="save every graded blob (default on; --no-preserve-pocs to disable)")
    # Public benchmark is always blind; normal (hinted) mode is removed from the
    # public repo (it exists only in the private answers repo). Kept as a no-op.
    ap.add_argument("--full-scan", action="store_true", default=True,
                    help=argparse.SUPPRESS)
    ap.add_argument("--max-turns", type=int, default=100,
                    help="turn budget per episode (default 100 for full-scan; diff-scan uses 50)")
    ap.add_argument("--timeout", type=int, default=1800, help="per-episode seconds")
    ap.add_argument("--output", "-o", default=None,
                    help="where results land (default: ./output). A bare name nests "
                         "under it (paper-v1 -> output/paper-v1); a path is used as-is "
                         "(/data/x, ./x). Cells: <output>/<bug>/<model>/seed-N/. "
                         "Re-running the same --output resumes it.")
    ap.add_argument("--report-only", action="store_true",
                    help="skip running; just re-aggregate from <output>/")
    ap.add_argument("--dashboard", dest="dashboard", action="store_true", default=None,
                    help="force the live full-screen dashboard (default: on when stdout is a TTY)")
    ap.add_argument("--no-dashboard", dest="dashboard", action="store_false",
                    help="disable the live dashboard; fall back to line-by-line logs")
    ap.add_argument("--jobs", "-j", type=int, default=1,
                    help="run N cells concurrently (default 1). Parallel runs use "
                         "line-by-line logs, not the live dashboard. Each cell is its "
                         "own subprocess + Docker container, graded independently — "
                         "but a high N can trip model rate limits (→ SDK backoff), so "
                         "4–6 is usually the sweet spot.")
    args = ap.parse_args()

    out = resolve_output(args.output)
    print(f"  output: {out}")
    models = resolve_models(args.models)
    bugs = resolve_bugs(args.bugs)
    if args.samples < 1:
        ap.error("--samples must be >= 1 (it is a repeat count, not a seed index)")
    samples = list(range(args.samples))  # N -> seed indices [0 .. N-1]

    if args.report_only:
        aggregate(out, models, bugs, samples)
        return 0

    # samples-major order: one full (model x bug) pass per sample, so repeats of
    # the same cell are spread across time (decorrelates transient API/model
    # conditions) instead of running back-to-back. Tuple stays (m, b, s) so all
    # downstream unpacking is unchanged; only iteration order differs.
    cells = [(m, b, s) for s in samples for m in models for b in bugs]
    done = sum(1 for m, b, s in cells if (cell_dir(out, b, m, s) / "score.json").is_file())
    print(f"  sweep: {len(models)} models x {len(bugs)} bugs x {len(samples)} samples "
          f"= {len(cells)} cells ({done} already done, {len(cells)-done} to run)")

    from rich.console import Console
    from fbbench.sweep.dashboard import STATUS, dashboard, run_cell_tailing
    console = Console()
    use_dash = args.dashboard if args.dashboard is not None else console.is_terminal
    STATUS.configure(exp=out.name, models=models, bugs=bugs, samples=samples,
                     max_turns=args.max_turns, full_scan=args.full_scan,
                     total=len(cells), already_done=done)

    jobs = max(1, args.jobs)
    t0 = time.time()

    if jobs > 1:
        # Parallel: each cell is an independent subprocess + Docker container,
        # graded independently by the remote oracle, so concurrency is safe.
        # subprocess.run releases the GIL → threads are enough. No live
        # dashboard in this mode (interleaved cells would scramble it).
        from concurrent.futures import ThreadPoolExecutor

        todo = [(i, m, b, s) for i, (m, b, s) in enumerate(cells, 1)
                if not (cell_dir(out, b, m, s) / "score.json").is_file()]
        print(f"  running {len(todo)} cells with {jobs} parallel workers "
              f"(line-by-line logs; {done} skipped as already done)", flush=True)

        def _run_one(item):
            i, model, bug, sample = item
            print(f"  [{i}/{len(cells)}] start {model} / {bug} / sample-{sample}", flush=True)
            r = run_cell(model, bug, sample, args.max_turns, out, args.timeout,
                         preserve_pocs=args.preserve_pocs, full_scan=args.full_scan)
            if r and "error" not in r:
                print(f"      -> [{bug}] {r.get('tier_score','?')}/5  "
                      f"{r.get('terminated_reason','')}  ${r.get('total_usd') or 0.0:.4f}",
                      flush=True)
            else:
                print(f"      -> [{bug}] FAILED: {r.get('error') if r else 'unknown'}", flush=True)
            return r

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            list(ex.map(_run_one, todo))
    else:
        with dashboard(console, enabled=use_dash):
            for i, (model, bug, sample) in enumerate(cells, 1):
                cd = cell_dir(out, bug, model, sample)
                if (cd / "score.json").is_file():
                    STATUS.cell_skip(model, bug, sample)
                    continue
                kb = bug_kb(bug)
                tag = f"[{i}/{len(cells)}] {model} / {bug} / sample-{sample}"
                if use_dash:
                    STATUS.cell_start(model, bug, sample, kb)
                    cmd = RUNNER + ["--bug", bug, "--model", model,
                                    "--max-turns", str(args.max_turns), "--out-dir", str(cd)]
                    cmd.append("--preserve-pocs" if args.preserve_pocs else "--no-preserve-pocs")
                    if args.full_scan:
                        cmd.append("--full-scan")
                    r = run_cell_tailing(cmd, str(REPO), args.timeout,
                                         cd / "episode.jsonl", model, bug, sample)
                    STATUS.cell_finish(model, bug, sample, r)
                else:
                    print(f"  {tag} ...", flush=True)
                    r = run_cell(model, bug, sample, args.max_turns, out, args.timeout,
                                 preserve_pocs=args.preserve_pocs, full_scan=args.full_scan)
                    if r and "error" not in r:
                        ts = r.get("tier_score", "?")
                        print(f"      -> {ts}/5  {r.get('terminated_reason','')}  "
                              f"${r.get('total_usd') or 0.0:.4f}", flush=True)
                    else:
                        print(f"      -> FAILED: {r.get('error') if r else 'unknown'}", flush=True)

    elapsed = time.time() - t0
    # STATUS.total_cost is only fed by the dashboard path; in parallel/line mode
    # sum straight from the cells on disk so the figure is always right.
    spent = STATUS.total_cost
    if jobs > 1 or not spent:
        spent = 0.0
        for m in models:
            for b in bugs:
                for s in samples:
                    sj = cell_dir(out, b, m, s) / "score.json"
                    if sj.is_file():
                        try:
                            spent += float(json.loads(sj.read_text()).get("total_usd") or 0.0)
                        except (OSError, ValueError):
                            pass
    print(f"\n  done in {elapsed:.0f}s, spent ~${spent:.2f} total (all cells on disk)")
    aggregate(out, models, bugs, samples)

    # Self-contained, answer-free summary page for the whole sweep.
    try:
        from fbbench.report import write_summary
        idx = write_summary(out, exp=out.name, models=models, bugs=bugs, samples=samples,
                            max_turns=args.max_turns, full_scan=args.full_scan,
                            elapsed_s=elapsed)
        print(f"  summary: {idx}")
    except Exception as e:  # noqa: BLE001
        print(f"  (summary generation skipped: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
