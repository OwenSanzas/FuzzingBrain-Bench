"""The run engine behind `fb-bench run`.

`run_matrix()` runs a (models x bugs x samples) matrix through
`python -m fbbench.runner`, one episode per subprocess (isolated + per-episode
timeout), resumable (skips cells whose score.json already exists), with a live
cost tally and a final leaderboard. A single run is just a size-1 matrix, so
`fb-bench run` (single or many) is the only front door — there is no separate
CLI here. Each cell lands at output/<bug>/<model>/seed-N/ where N is the sample
index (kept named `seed-N` for back-compat with the legacy 518-row dataset).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from fbbench.grading import (
    DEFAULT_GRADE_URL, DEFAULT_KB, capability_set, find_bug, graded_flags, list_bugs,
)
from fbbench.models import SUPPORTED_MODELS, default_sweep
from fbbench.paths import REPO, resolve_output

RUNNER = [sys.executable, "-m", "fbbench.runner"]


def discover_bugs() -> list[str]:
    return [name for name, _ in list_bugs()]


def resolve_models(spec: str) -> list[str]:
    # 'default-lineup' = the curated cross-model roster (see models.default_sweep).
    # 'sweep' kept as a silent back-compat alias for the old flag value.
    if spec in ("default-lineup", "sweep"):
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
    518 existing data points. It still does not drive sampling — it is which
    repeat this is, forwarded to the runner as --seed so the oracle can tell
    one cell's repeats apart in its own records."""
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


def _denom(score: dict) -> int:
    """Tier denominator for one cell: the rungs its bug is graded on, not 5."""
    return len(graded_flags(score.get("capabilities") or {},
                            (score.get("config") or {}).get("capability_set")))


def bug_kb(bug: str) -> list[str]:
    """The capability_set (required flags) for a bug, from its bench.yaml."""
    bd = find_bug(bug)
    return capability_set(bd) if bd else list(DEFAULT_KB)


def cell_cmd(model: str, bug: str, cd: Path, max_turns: int, *,
             seed: int | None = None, batch: str | None = None,
             preserve_pocs: bool = True, stop_on_solve: bool = True,
             api_key: str | None = None, image_prefix: str | None = None,
             runner: list[str] | None = None) -> list[str]:
    """The exact `python -m fbbench.runner` argv for one cell. Single source of
    truth so the single and multi paths forward the SAME per-cell flags."""
    cmd = (runner or RUNNER) + ["--bug", bug, "--model", model,
                                "--max-turns", str(max_turns), "--out-dir", str(cd)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if batch:
        cmd += ["--batch", batch]
    cmd.append("--preserve-pocs" if preserve_pocs else "--no-preserve-pocs")
    if not stop_on_solve:
        cmd.append("--no-stop-on-solve")
    if api_key:
        cmd += ["--api-key", api_key]
    if image_prefix:
        cmd += ["--image-prefix", image_prefix]
    return cmd


def run_cell(model: str, bug: str, sample: int, max_turns: int, out: Path,
             timeout: int, preserve_pocs: bool = True, *,
             stop_on_solve: bool = True, api_key: str | None = None,
             image_prefix: str | None = None, runner: list[str] | None = None,
             batch: str | None = None) -> dict | None:
    cd = cell_dir(out, bug, model, sample)
    cmd = cell_cmd(model, bug, cd, max_turns, seed=sample, batch=batch,
                   preserve_pocs=preserve_pocs, stop_on_solve=stop_on_solve,
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


def register_batch(name: str, params: dict) -> str:
    """Mint an id for this sweep and tell the oracle what flags defined it.

    Returns the id whether or not registration succeeded. Best-effort on purpose:
    a sweep that cannot reach the grader should still run — it loses the ability
    to explain its own numbers later, which is worse than not running only if you
    squint. The cells carry the id regardless, so the grades still group.
    """
    batch_uid = uuid.uuid4().hex
    url = os.environ.get("BENCH_GRADE_URL", DEFAULT_GRADE_URL).rstrip("/")
    body = json.dumps({"batch_uid": batch_uid, "name": name, "params": params}).encode()
    req = urllib.request.Request(
        f"{url}/v1/batches", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "ngrok-skip-browser-warning": "true"})
    try:
        urllib.request.urlopen(req, timeout=30).close()
    except Exception as e:  # noqa: BLE001 — nothing here is worth failing a sweep for
        print(f"  note: could not register this batch ({e}); results will still group "
              f"under {batch_uid[:12]} but their flags will not be recorded")
    return batch_uid


def run_matrix(models: list[str], bugs: list[str], *, samples: int = 1,
               output: str | None = None, max_turns: int = 100, timeout: int = 1800,
               jobs: int = 1, dashboard_pref: bool | None = None,
               preserve_pocs: bool = True, stop_on_solve: bool = True,
               api_key: str | None = None, image_prefix: str | None = None,
               report_only: bool = False, runner: list[str] | None = None,
               arm: str = "api", auth: str = "sub",
               model_map: dict[str, str] | None = None) -> int:
    """THE engine: run the (models x bugs x samples) matrix. One code path for
    both a single cell (len 1) and a full sweep (len N) — a single run is just a
    matrix of size one. `fb-bench run` (every arm) calls this.

    `arm` selects the per-cell executor, all sharing the SAME matrix machinery
    (resume / parallel / aggregate / report):
      * api        — drive a provider model via `python -m fbbench.runner`
      * codex      — drive OpenAI's codex CLI (fbbench.sweep.codex.run_cell)
      * claudecode — drive the Claude Code CLI (fbbench.sweep.claudecode.run_cell)
    `model_map` maps a cell label back to the raw model id an arm needs (the API
    arm uses labels verbatim; claudecode labels differ from the raw claude id).
    """
    if samples < 1:
        raise ValueError("samples must be >= 1 (a repeat count, not a seed index)")
    # One run, one self-contained folder, never a collision — named and un-named
    # runs behave identically:
    #   * no --output   -> an auto name output/run_<timestamp>
    #   * --output NAME -> output/NAME, but if that already exists a real run
    #                      forks output/NAME_<timestamp> instead of resuming into
    #                      (or overwriting) the earlier campaign.
    # A fresh timestamp never collides, so the summary always lives in this run's
    # own folder and no two runs share results. --report-only is the sole reader:
    # it must open the existing folder in place, so it never forks.
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if output is None:
        out = resolve_output(f"run_{ts}")
    else:
        out = resolve_output(output)
        if not report_only and out.exists():
            out = out.parent / f"{out.name}_{ts}"
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

    # One id for the whole matrix, so every grade it produces rolls up to the
    # flags that defined it. A resumed sweep gets a NEW id: it is a different
    # experiment in wall-clock terms even when the cells are the same.
    batch_uid = register_batch(out.name, {
        "models": models, "bugs": len(bugs), "samples": samples,
        "max_turns": max_turns, "timeout": timeout, "jobs": jobs, "arm": arm,
        "stop_on_solve": stop_on_solve, "output": str(out),
    })

    from rich.console import Console
    from fbbench.sweep.dashboard import STATUS, dashboard, run_cell_tailing
    console = Console()
    # The live dashboard tails the API runner's episode.jsonl; the vendor-CLI
    # arms don't produce that live stream, so only the API arm gets the dashboard.
    _dash_pref = dashboard_pref if dashboard_pref is not None else console.is_terminal
    use_dash = (arm == "api") and _dash_pref
    if arm != "api" and _dash_pref:
        # A non-blocking heads-up: the run proceeds with line-by-line logs, and
        # the full transcript + report.html are still written per cell.
        print(f"  note: the live dashboard is not available for --arm {arm} yet — "
              f"using line-by-line logs (per-cell transcript + report are still produced).",
              flush=True)
    STATUS.configure(exp=out.name, models=models, bugs=bugs, samples=seeds,
                     max_turns=max_turns, total=len(cells), already_done=done)

    def _cell(model, bug, sample):
        # Per-cell dispatch by arm — every arm writes score.json into the SAME
        # cell dir, so resume / aggregate / report downstream are arm-agnostic.
        #
        # Isolate per-cell failures: an arm that RAISES (e.g. the grade oracle is
        # unreachable, per _best_caps) must NOT kill the whole matrix — otherwise a
        # single bad cell discards the aggregate + report for every cell that DID
        # finish. Catch it, record the error, and let the run continue. No score.json
        # is written on the failing path (the arms write it only after grading), so
        # resume simply re-runs the cell next time instead of freezing a false zero.
        try:
            if arm == "codex":
                from fbbench.sweep import codex
                raw = (model_map or {}).get(model, model)
                return codex.run_cell(cell_dir(out, bug, model, sample), bug, timeout,
                                      max_turns, model=raw, auth=auth, api_key=api_key,
                                      preserve_pocs=preserve_pocs)
            if arm == "claudecode":
                from fbbench.sweep import claudecode
                raw = (model_map or {}).get(model, model)
                return claudecode.run_cell(cell_dir(out, bug, model, sample), bug, raw,
                                           timeout, max_turns, auth=auth, api_key=api_key,
                                           preserve_pocs=preserve_pocs)
            return run_cell(model, bug, sample, max_turns, out, timeout,
                            preserve_pocs=preserve_pocs, stop_on_solve=stop_on_solve,
                            api_key=api_key, image_prefix=image_prefix, runner=runner,
                            batch=batch_uid)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return {"error": f"{type(e).__name__}: {e}"}

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
                print(f"      -> [{bug}] {r.get('tier_score','?')}/{_denom(r)}  "
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
                    cmd = cell_cmd(model, bug, cd, max_turns, seed=sample, batch=batch_uid,
                                   preserve_pocs=preserve_pocs,
                                   stop_on_solve=stop_on_solve,
                                   api_key=api_key, image_prefix=image_prefix, runner=runner)
                    r = run_cell_tailing(cmd, str(REPO), timeout,
                                         cd / "episode.jsonl", model, bug, sample)
                    STATUS.cell_finish(model, bug, sample, r)
                else:
                    print(f"  {tag} ...", flush=True)
                    r = _cell(model, bug, sample)
                    if r and "error" not in r:
                        print(f"      -> {r.get('tier_score','?')}/{_denom(r)}  {r.get('terminated_reason','')}  "
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
                            max_turns=max_turns, elapsed_s=elapsed)
        print(f"  summary: {idx}")
    except Exception as e:  # noqa: BLE001
        print(f"  (summary generation skipped: {e})")
    return 0
