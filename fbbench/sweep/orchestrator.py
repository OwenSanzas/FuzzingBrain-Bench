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


def cell_cmd(model: str, bug: str, cd: Path, max_turns: int, *,
             preserve_pocs: bool = True, full_scan: bool = True,
             stop_on_solve: bool = True, api_key: str | None = None,
             image_prefix: str | None = None, runner: list[str] | None = None) -> list[str]:
    """The exact `python -m fbbench.runner` argv for one cell. Single source of
    truth so the single and multi paths forward the SAME per-cell flags."""
    cmd = (runner or RUNNER) + ["--bug", bug, "--model", model,
                                "--max-turns", str(max_turns), "--out-dir", str(cd)]
    cmd.append("--preserve-pocs" if preserve_pocs else "--no-preserve-pocs")
    if not stop_on_solve:
        cmd.append("--no-stop-on-solve")
    if full_scan:
        cmd.append("--full-scan")
    if api_key:
        cmd += ["--api-key", api_key]
    if image_prefix:
        cmd += ["--image-prefix", image_prefix]
    return cmd


def run_cell(model: str, bug: str, sample: int, max_turns: int, out: Path,
             timeout: int, preserve_pocs: bool = True, full_scan: bool = True,
             *, stop_on_solve: bool = True, api_key: str | None = None,
             image_prefix: str | None = None, runner: list[str] | None = None) -> dict | None:
    cd = cell_dir(out, bug, model, sample)
    cmd = cell_cmd(model, bug, cd, max_turns, preserve_pocs=preserve_pocs,
                   full_scan=full_scan, stop_on_solve=stop_on_solve,
                   api_key=api_key, image_prefix=image_prefix, runner=runner)
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


def run_matrix(models: list[str], bugs: list[str], *, samples: int = 1,
               output: str | None = None, max_turns: int = 100, timeout: int = 1800,
               jobs: int = 1, dashboard_pref: bool | None = None,
               preserve_pocs: bool = True, full_scan: bool = True,
               stop_on_solve: bool = True, api_key: str | None = None,
               image_prefix: str | None = None, report_only: bool = False,
               runner: list[str] | None = None) -> int:
    """THE engine: run the (models x bugs x samples) matrix. One code path for
    both a single cell (len 1) and a full sweep (len N) — a single run is just a
    matrix of size one. `fb-bench run` and the module __main__ both call this."""
    if samples < 1:
        raise ValueError("samples must be >= 1 (a repeat count, not a seed index)")
    out = resolve_output(output)
    print(f"  output: {out}")
    seeds = list(range(samples))  # N -> seed indices [0 .. N-1]

    if report_only:
        aggregate(out, models, bugs, seeds)
        return 0

    # samples-major order: one full (model x bug) pass per sample, so repeats of
    # the same cell are spread across time (decorrelates transient conditions).
    cells = [(m, b, s) for s in seeds for m in models for b in bugs]
    done = sum(1 for m, b, s in cells if (cell_dir(out, b, m, s) / "score.json").is_file())
    print(f"  {len(models)} model(s) x {len(bugs)} bug(s) x {samples} sample(s) "
          f"= {len(cells)} cell(s) ({done} already done, {len(cells)-done} to run)")

    from rich.console import Console
    from fbbench.sweep.dashboard import STATUS, dashboard, run_cell_tailing
    console = Console()
    use_dash = dashboard_pref if dashboard_pref is not None else console.is_terminal
    STATUS.configure(exp=out.name, models=models, bugs=bugs, samples=seeds,
                     max_turns=max_turns, full_scan=full_scan,
                     total=len(cells), already_done=done)

    def _cell(model, bug, sample):
        return run_cell(model, bug, sample, max_turns, out, timeout,
                        preserve_pocs=preserve_pocs, full_scan=full_scan,
                        stop_on_solve=stop_on_solve, api_key=api_key,
                        image_prefix=image_prefix, runner=runner)

    jobs = max(1, jobs)
    t0 = time.time()

    if jobs > 1:
        # Parallel: each cell is an independent subprocess + Docker container,
        # graded independently by the remote oracle, so concurrency is safe.
        from concurrent.futures import ThreadPoolExecutor

        todo = [(i, m, b, s) for i, (m, b, s) in enumerate(cells, 1)
                if not (cell_dir(out, b, m, s) / "score.json").is_file()]
        print(f"  running {len(todo)} cell(s) with {jobs} parallel workers "
              f"(line-by-line logs; {done} skipped as already done)", flush=True)

        def _run_one(item):
            i, model, bug, sample = item
            print(f"  [{i}/{len(cells)}] start {model} / {bug} / sample-{sample}", flush=True)
            r = _cell(model, bug, sample)
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
                    cmd = cell_cmd(model, bug, cd, max_turns, preserve_pocs=preserve_pocs,
                                   full_scan=full_scan, stop_on_solve=stop_on_solve,
                                   api_key=api_key, image_prefix=image_prefix, runner=runner)
                    r = run_cell_tailing(cmd, str(REPO), timeout,
                                         cd / "episode.jsonl", model, bug, sample)
                    STATUS.cell_finish(model, bug, sample, r)
                else:
                    print(f"  {tag} ...", flush=True)
                    r = _cell(model, bug, sample)
                    if r and "error" not in r:
                        print(f"      -> {r.get('tier_score','?')}/5  {r.get('terminated_reason','')}  "
                              f"${r.get('total_usd') or 0.0:.4f}", flush=True)
                    else:
                        print(f"      -> FAILED: {r.get('error') if r else 'unknown'}", flush=True)

    elapsed = time.time() - t0
    spent = STATUS.total_cost
    if jobs > 1 or not spent:
        spent = 0.0
        for m in models:
            for b in bugs:
                for s in seeds:
                    sj = cell_dir(out, b, m, s) / "score.json"
                    if sj.is_file():
                        try:
                            spent += float(json.loads(sj.read_text()).get("total_usd") or 0.0)
                        except (OSError, ValueError):
                            pass
    print(f"\n  done in {elapsed:.0f}s, spent ~${spent:.2f} total (all cells on disk)")
    aggregate(out, models, bugs, seeds)

    # Self-contained, answer-free summary page.
    try:
        from fbbench.report import write_summary
        idx = write_summary(out, exp=out.name, models=models, bugs=bugs, samples=seeds,
                            max_turns=max_turns, full_scan=full_scan, elapsed_s=elapsed)
        print(f"  summary: {idx}")
    except Exception as e:  # noqa: BLE001
        print(f"  (summary generation skipped: {e})")
    return 0


def main() -> int:
    """Deprecated direct entry — `fb-bench run` is the front door. Kept as a thin
    wrapper over run_matrix() for back-compat."""
    ap = argparse.ArgumentParser(description="FuzzingBrain Bench batch sweep "
                                             "(use `fb-bench run` instead)")
    ap.add_argument("--models", default="claude-opus-4-7")
    ap.add_argument("--bugs", default="all")
    ap.add_argument("--samples", type=int, default=1, metavar="N")
    ap.add_argument("--preserve-pocs", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--full-scan", action="store_true", default=True, help=argparse.SUPPRESS)
    ap.add_argument("--max-turns", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--output", "-o", default=None)
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--dashboard", dest="dashboard", action="store_true", default=None)
    ap.add_argument("--no-dashboard", dest="dashboard", action="store_false")
    ap.add_argument("--jobs", "-j", type=int, default=1)
    args = ap.parse_args()
    return run_matrix(resolve_models(args.models), resolve_bugs(args.bugs),
                      samples=args.samples, output=args.output, max_turns=args.max_turns,
                      timeout=args.timeout, jobs=args.jobs, dashboard_pref=args.dashboard,
                      preserve_pocs=args.preserve_pocs, full_scan=args.full_scan,
                      report_only=args.report_only)


if __name__ == "__main__":
    sys.exit(main())
