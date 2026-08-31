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
import subprocess
import sys
import time
from pathlib import Path

from fbbench.grading import list_bugs
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
    want = [m.strip() for m in spec.split(",") if m.strip()]
    unknown = [m for m in want if m not in SUPPORTED_MODELS]
    if unknown:
        sys.exit(f"unknown model(s): {', '.join(unknown)} "
                 f"(see `fb-bench models`)")
    return want


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
    repeat this is, forwarded to the runner as --seed so one cell's repeats
    can be told apart."""
    return out / bug / model / f"seed-{sample}"


# The subprocess timeout is a BACKSTOP only: the episode owns its wall-clock
# budget (--timeout) and stops itself gracefully so it can write score.json. We
# give the subprocess this much extra headroom to finish that writeout + docker
# teardown before we SIGKILL it out from under a hung run.
_SUBPROC_BACKSTOP_S = 180

# Where a cell's runner subprocess sends its stderr. Both cell paths (plain and
# dashboard-tailing) send it to a FILE rather than a pipe, because nobody read
# the pipe: the output was discarded on success and LOST on failure, and a child
# that wrote more than the pipe buffer would block until the backstop killed it.
# A cell that dies before writing score.json (docker unreachable, bad API key,
# image pull refused) reported only "no score.json" — the symptom, with the
# actual cause thrown away.
_STDERR_LOG = "runner.stderr.log"


def _cell_failure(cd: Path) -> dict:
    """Why this cell produced no score.json, read back from its stderr log.

    The last non-empty line is the useful one: a Python traceback ends with its
    exception line and a failing CLI ends with its message. The whole log stays
    on disk beside the cell, so this one-liner can stay short enough for the
    dashboard's `recent` row while the full trace is one `cat` away.
    """
    log = cd / _STDERR_LOG
    try:
        lines = [ln.strip() for ln in log.read_text(errors="replace").splitlines()
                 if ln.strip()]
    except OSError:
        lines = []
    if not lines:
        return {"error": "no score.json (the runner wrote nothing to stderr)"}
    return {"error": lines[-1][:300], "error_log": str(log)}



def _usd(r: dict | None) -> str:
    """Render a cell's cost. An unknown cost is NOT zero: an external agent that
    reports no usage must not be indistinguishable from a run that was free."""
    v = (r or {}).get("total_usd")
    return f"${v:.4f}" if isinstance(v, (int, float)) else "$ —"


def _failed_line(r: dict | None) -> str:
    """The one-line reason a cell failed, plus where to read the whole thing."""
    msg = (r or {}).get("error") or "unknown"
    log = (r or {}).get("error_log")
    return f"FAILED: {msg}" + (f"\n         log: {log}" if log else "")


def _tidy_stderr_log(cd: Path) -> None:
    """Drop an empty stderr log — a cell that ran clean leaves no debris."""
    log = cd / _STDERR_LOG
    try:
        if log.stat().st_size == 0:
            log.unlink()
    except OSError:
        pass


def cell_cmd(model: str, bug: str, cd: Path, max_turns: int, *,
             timeout: int | None = None,
             seed: int | None = None,
             preserve_pocs: bool = True, stop_on_crash: bool = False,
             api_key: str | None = None, image_prefix: str | None = None,
             runner: list[str] | None = None) -> list[str]:
    """The exact `python -m fbbench.runner` argv for one cell. Single source of
    truth so the single and multi paths forward the SAME per-cell flags."""
    cmd = (runner or RUNNER) + ["--bug", bug, "--model", model,
                                "--max-turns", str(max_turns), "--out-dir", str(cd)]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    cmd.append("--preserve-pocs" if preserve_pocs else "--no-preserve-pocs")
    if not stop_on_crash:
        cmd.append("--no-stop-on-crash")
    if api_key:
        cmd += ["--api-key", api_key]
    if image_prefix:
        cmd += ["--image-prefix", image_prefix]
    return cmd


def run_cell(model: str, bug: str, sample: int, max_turns: int, out: Path,
             timeout: int, preserve_pocs: bool = True, *,
             stop_on_crash: bool = False, api_key: str | None = None,
             image_prefix: str | None = None,
             runner: list[str] | None = None,
             ) -> dict | None:
    cd = cell_dir(out, bug, model, sample)
    cmd = cell_cmd(model, bug, cd, max_turns, timeout=timeout,
                   seed=sample,
                   preserve_pocs=preserve_pocs, stop_on_crash=stop_on_crash,
                   api_key=api_key, image_prefix=image_prefix,
                   runner=runner)
    cd.mkdir(parents=True, exist_ok=True)
    try:
        # The episode self-stops at `timeout`; SIGKILL only if it overruns the
        # graceful-writeout backstop (so a finished run isn't lost to the killer).
        with (cd / _STDERR_LOG).open("wb") as errlog:
            subprocess.run(cmd, cwd=REPO, timeout=timeout + _SUBPROC_BACKSTOP_S,
                           stdout=subprocess.DEVNULL, stderr=errlog)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "error_log": str(cd / _STDERR_LOG)}
    sj = cd / "score.json"
    if sj.is_file():
        _tidy_stderr_log(cd)
        return json.loads(sj.read_text())
    return _cell_failure(cd)


def aggregate(out: Path, models: list[str], bugs: list[str], seeds: list[int]) -> None:
    """Print the per-model leaderboard: distinct crashes, coverage, cost."""
    print("\n" + "=" * 78)
    print(f"  {'model':24s} {'uniqCr':>7s} {'chall':>7s} {'crashed':>8s} "
          f"{'refus':>6s} {'cost$':>8s}")
    print("  " + "-" * 90)
    for model in models:
        refusals = n = crashed_on = 0
        crashes = 0   # headline: DISTINCT crashes, best-of-seeds per bug, summed
        cost = 0.0
        for bug in bugs:
            seen = False
            # Best (max) across this bug's seeds, never the sum: repeats measure
            # consistency, not capability, so summing would make the score a
            # function of how many samples were bought.
            bug_crashes = 0
            for seed in seeds:
                sj = cell_dir(out, bug, model, seed) / "score.json"
                if not sj.is_file():
                    continue
                seen = True
                s = json.loads(sj.read_text())
                bug_crashes = max(bug_crashes, int(s.get("unique_crashes", 0)))
                if s.get("terminated_reason") == "refusal":
                    refusals += 1
                if s.get("total_usd"):
                    cost += s["total_usd"]
            if not seen:
                continue
            n += 1
            crashes += bug_crashes
            if bug_crashes:
                crashed_on += 1
        print(f"  {model:24s} {crashes:>7d} {n:>7d} {crashed_on:>8d} "
              f"{refusals:>6d} {cost:>8.2f}")
    print("=" * 90)


def _write_summary(out: Path, models: list[str], bugs: list[str], seeds: list[int],
                   max_turns: int, elapsed_s: float | None) -> None:
    """Write the self-contained, answer-free summary page. Never fatal: a run
    that finished should not be reported as failed because its page could not be
    rendered."""
    try:
        from fbbench.report import write_summary
        idx = write_summary(out, exp=out.name, models=models, bugs=bugs, samples=seeds,
                            max_turns=max_turns, elapsed_s=elapsed_s)
        print(f"  summary: {idx}")
    except Exception as e:  # noqa: BLE001
        print(f"  (summary generation skipped: {e})")


def run_matrix(models: list[str], bugs: list[str], *, samples: int = 1,
               output: str | None = None, max_turns: int = 100, timeout: int = 1800,
               jobs: int = 1, dashboard_pref: bool | None = None,
               preserve_pocs: bool = True, stop_on_crash: bool = False,
               api_key: str | None = None, image_prefix: str | None = None,
                 report_only: bool = False, runner: list[str] | None = None,
               arm: str = "api", auth: str = "sub",
               model_map: dict[str, str] | None = None,
                 agent_manifest: str | None = None) -> int:
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
        # Rebuild index.html too. --report-only used to print the leaderboard and
        # leave the page as it was, so a page written before a scoring change kept
        # showing the old numbers with no sign it was stale.
        _write_summary(out, models, bugs, seeds, max_turns, elapsed_s=None)
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
        # Isolate per-cell failures: an arm that RAISES (e.g. grading a candidate
        # blob failed, per codex._crash_signatures) must NOT kill the matrix — a
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
            if arm == "external":
                from fbbench.sweep.external import Manifest, run_cell as ext_run_cell
                mani = Manifest.load(agent_manifest)
                raw = (model_map or {}).get(model, model)
                return ext_run_cell(cell_dir(out, bug, model, sample), bug, raw,
                                    timeout, max_turns, manifest=mani,
                                    api_key=api_key, preserve_pocs=preserve_pocs)
            return run_cell(model, bug, sample, max_turns, out, timeout,
                            preserve_pocs=preserve_pocs, stop_on_crash=stop_on_crash,
                            api_key=api_key, image_prefix=image_prefix, runner=runner,
                            )
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return {"error": f"{type(e).__name__}: {e}"}

    jobs = max(1, jobs)
    t0 = time.time()
    # Cells that never produced a score.json, with the reason their runner gave.
    # Reprinted after the run because the live dashboard tears its panels down,
    # taking the only on-screen copy of the error with it — which is how a run
    # where every cell died on the same fault still ended in a clean-looking
    # all-zeros leaderboard.
    failures: list[tuple[str, str, int, dict | None]] = []

    if jobs > 1:
        # Parallel: each cell is an independent subprocess + Docker container,
        # grading inside its own, so concurrency is safe.
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
                print(f"      -> [{bug}] {r.get('unique_crashes','?')} crashes  "
                      f"{r.get('terminated_reason','')}  {_usd(r)}", flush=True)
            else:
                print(f"      -> [{bug}] {_failed_line(r)}", flush=True)
                failures.append((model, bug, sample, r))
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
                tag = f"[{i}/{len(cells)}] {model} / {bug} / sample-{sample}"
                if use_dash:
                    STATUS.cell_start(model, bug, sample)
                    cmd = cell_cmd(model, bug, cd, max_turns, timeout=timeout,
                                   seed=sample,
                                   preserve_pocs=preserve_pocs,
                                   stop_on_crash=stop_on_crash,
                                   api_key=api_key, image_prefix=image_prefix, runner=runner)
                    r = run_cell_tailing(cmd, str(REPO), timeout,
                                         cd / "episode.jsonl", model, bug, sample)
                    STATUS.cell_finish(model, bug, sample, r)
                    if not r or "error" in r:
                        failures.append((model, bug, sample, r))
                else:
                    print(f"  {tag} ...", flush=True)
                    r = _cell(model, bug, sample)
                    if r and "error" not in r:
                        print(f"      -> {r.get('unique_crashes','?')} crashes  "
                              f"{r.get('terminated_reason','')}  {_usd(r)}", flush=True)
                    else:
                        print(f"      -> {_failed_line(r)}", flush=True)
                        failures.append((model, bug, sample, r))

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
    if failures:
        print(f"\n  {len(failures)} cell(s) produced no score.json:")
        for model, bug, sample, r in failures:
            print(f"    {bug} / {model} / sample-{sample}")
            print(f"      {(r or {}).get('error') or 'unknown'}")
            if (r or {}).get("error_log"):
                print(f"      log: {r['error_log']}")
    aggregate(out, models, bugs, seeds)

    _write_summary(out, models, bugs, seeds, max_turns, elapsed_s=elapsed)
    return 0
