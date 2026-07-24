"""CLI entrypoint for the episode driver — `python -m fbbench.runner`.

One invocation = one (model, bug) episode written to --out-dir. Most users go
through `fb-bench run` (which wraps this, picks a model from .env, and creates
a unique output dir); the batch sweep also shells out to this entry per cell.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from fbbench.env import load_dotenv
from fbbench.grading.bench_yaml import capability_set, find_bug, read_bench
from fbbench.models import CATALOG, PRICES, cost_usd, default_sweep
from fbbench.paths import REPO
from fbbench.runner.backends import make_backend
from fbbench.runner.episode import run_episode
from fbbench.runner.mcp_client import stage_bug_view, _full_scan_alias


def print_models() -> None:
    sweep = set(default_sweep())
    print(f"\n  {len(CATALOG)} supported models "
          "(any other provider id is still runnable via --model)\n")
    print(f"  {'model':26s} {'provider':10s} {'tier':9s} "
          f"{'in $/M':>7s} {'out $/M':>8s}  sweep")
    print(f"  {'-'*26} {'-'*10} {'-'*9} {'-'*7} {'-'*8}  -----")
    for model, provider, tier in CATALOG:
        rate = PRICES.get(model)
        ins = f"{rate[0]:.2f}" if rate else "?"
        outs = f"{rate[1]:.2f}" if rate else "?"
        mark = "✓" if model in sweep else ""
        print(f"  {model:26s} {provider:10s} {tier:9s} {ins:>7s} {outs:>8s}  {mark}")
    print("\n  default sweep (--model omitted in batch): " + ", ".join(default_sweep()))
    print()


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m fbbench.runner",
                                 description="FuzzingBrain Bench episode driver")
    ap.add_argument("--bug", help="challenge alias (e.g. net-snmp-02)")
    ap.add_argument("--model", default="claude-opus-4-7", help="model id (claude*/gpt*/gemini*)")
    ap.add_argument("--max-turns", type=int, default=100,
                    help="turn budget per episode (default 100 for full-scan; diff-scan uses 50)")
    ap.add_argument("--output", default="output", help="output root (legacy nesting <output>/<bug>/<model>/)")
    ap.add_argument("--out-dir", default=None,
                    help="literal output dir; takes precedence over --output")
    ap.add_argument("--preserve-pocs", action=argparse.BooleanOptionalAction, default=True,
                    help="save every graded candidate blob into pocs/{solved,failed}/ "
                         "(default on; pass --no-preserve-pocs to disable)")
    ap.add_argument("--stop-on-solve", action=argparse.BooleanOptionalAction, default=True,
                    help="end the episode when the target defect is first reproduced "
                         "(default on; --no-stop-on-solve lets the agent keep hunting "
                         "for more crashes until it stops (ASSESSMENT COMPLETE) or "
                         "--max-turns)")
    # The public benchmark is ALWAYS blind (full-scan): the bug description is
    # withheld and the agent must discover a crashing input. Normal (hinted) mode
    # is removed from the public repo — it exists only in the private answers repo.
    # `--full-scan` is kept as an accepted no-op (callers/orchestrator pass it).
    ap.add_argument("--full-scan", action="store_true", default=True,
                    help=argparse.SUPPRESS)
    ap.add_argument("--server-bin", default=None,
                    help="path to mcp-server binary (default: ./bin/mcp-server)")
    ap.add_argument("--repo-root", default=None,
                    help="benchmark repo root (default: auto-detected)")
    ap.add_argument("--oracle-dir", default=None,
                    help="override the oracle bug dir (grader + ground-truth binaries) while "
                         "keeping the agent-facing source view from the real bundle. Used by the "
                         "off-target ablation to swap in an interference-free oracle binary (Arm B). "
                         "The source view is identical, so the swap is invisible to the agent.")
    ap.add_argument("--api-key", default=None, help="provider API key (or use the env var)")
    ap.add_argument("--local", action="store_true",
                    help="DEV ONLY: drive a host mcp-server graded against the local "
                         "oracle. The default (canonical) path drives the PUBLIC challenge "
                         "image + remote oracle — identical to what any external user runs, "
                         "so reported scores are reproducible. Local grading can diverge.")
    ap.add_argument("--image-prefix", default="docker.io/osanzas/fbbench-challenge-",
                    help="registry prefix for the canonical challenge images")
    ap.add_argument("--list-models", action="store_true",
                    help="print the supported-model catalog and exit")
    args = ap.parse_args()

    if args.list_models:
        print_models()
        return 0
    if not args.bug:
        ap.error("--bug is required (or use --list-models)")

    repo_root = Path(args.repo_root) if args.repo_root else REPO
    load_dotenv(repo_root)
    server_bin = args.server_bin or str(repo_root / "bin" / "mcp-server")
    if args.local and not Path(server_bin).is_file():
        print(f"error: mcp-server binary not found at {server_bin}; build with:", file=sys.stderr)
        print(f"  go -C {repo_root}/tools/mcp-server build -o {server_bin}", file=sys.stderr)
        return 2

    bug_dir = find_bug(args.bug, repo_root)
    if bug_dir is None:
        print(f"error: bug {args.bug} not found under {repo_root}/bugs", file=sys.stderr)
        return 2

    # Canonical path (default): the agent runs against the PUBLIC challenge image
    # and grades via the remote oracle baked into it — the same artifact the world
    # runs. `--local` is a dev shortcut whose local grading can diverge.
    #
    # A bug may pin its own image via the optional top-level `image:` field in
    # bench.yaml (a full ref, e.g. docker.io/zhicheng/my-cairo-bug:latest); used
    # verbatim when set. Otherwise fall back to <image_prefix><alias> under the
    # canonical namespace.
    bug_image = read_bench(Path(bug_dir) / "bench.yaml").get("image")
    image = None if args.local else (bug_image or
                                     f"{args.image_prefix}{_full_scan_alias(str(bug_dir))}")
    out_dir = (Path(args.out_dir) if args.out_dir
               else Path(args.output) / args.bug / args.model)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The agent sees workspace_path via setup(). In full-scan the descriptive
    # bug id names the fault (e.g. "...-nonobject-oob"), so the workspace must NOT
    # be named after it there — keep it neutral. Normal mode reveals the bug in
    # the description anyway, so a bug-named dir is fine (and aids debugging).
    ws_prefix = "fbbench-fullscan-" if args.full_scan else f"fbbench-{args.bug}-"
    backend = make_backend(args.model, api_key=args.api_key)
    pocs_dir = (out_dir / "pocs") if args.preserve_pocs else None
    if image:
        # Canonical: everything (challenge surface, workspace, remote grading) is
        # inside the image. The host stages nothing.
        workspace, bug_view = None, None
        ep_bug_dir = "/src"
    else:
        workspace = tempfile.mkdtemp(prefix=ws_prefix)
        # Agent sees a staged sandbox (no grader/, poc/, binaries/); the grader
        # reads the answer key + ground-truth binaries from the real bug dir.
        bug_view = stage_bug_view(str(bug_dir), full_scan=args.full_scan)
        ep_bug_dir = bug_view
    try:
        result = run_episode(
            backend=backend,
            bug_id=args.bug,
            bug_dir=ep_bug_dir,
            oracle_dir=(args.oracle_dir or str(bug_dir)),
            workspace=workspace or "",
            server_bin=server_bin,
            image=image,
            max_turns=args.max_turns,
            episode_log=str(out_dir / "episode.jsonl"),
            capability_set=capability_set(bug_dir),
            pocs_dir=str(pocs_dir) if pocs_dir else None,
            stop_on_solve=args.stop_on_solve,
            full_scan=args.full_scan,
        )
    finally:
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        if bug_view:
            shutil.rmtree(bug_view, ignore_errors=True)

    score = {
        "bug_id": result.bug_id,
        "model": result.model,
        # Every run knob that shaped this episode — surfaced verbatim in the
        # report so a result is fully reproducible from its own score.json.
        "config": {
            "mode": "full-scan" if args.full_scan else "normal",
            "max_turns": args.max_turns,
            "full_scan": bool(args.full_scan),
            "stop_on_solve": bool(args.stop_on_solve),
            "preserve_pocs": bool(args.preserve_pocs),
            "grading": "local-oracle" if args.local else "remote-oracle",
            "image": image or "(host mcp-server, --local)",
            "capability_set": sorted(capability_set(bug_dir) or []),
        },
        "solved": result.solved,
        "capabilities": result.capabilities,
        "capabilities_bestof": result.capabilities_bestof,
        "tier_score": sum(1 for v in result.capabilities.values() if v == "fired"),
        "tier_score_bestof": sum(1 for v in result.capabilities_bestof.values() if v == "fired"),
        "terminated_reason": result.terminated_reason,
        "refusal_retries": result.refusal_retries,
        "malformed_retries": result.malformed_retries,
        "turns_used": result.turns_used,
        "duration_s": result.duration_s,
    }
    if result.error:
        score["error"] = result.error
    cost = {"model": result.model,
            **cost_usd(result.model, result.input_tokens, result.output_tokens,
                       result.cache_read_tokens, result.cache_write_tokens)}
    score["total_usd"] = cost["total_usd"]
    (out_dir / "score.json").write_text(json.dumps(score, indent=2))
    (out_dir / "cost.json").write_text(json.dumps(cost, indent=2))

    # Self-contained browsable report (best-effort; never fails the run).
    try:
        from fbbench.runner.report import write_report
        write_report(out_dir)
    except Exception:  # noqa: BLE001
        pass

    print(json.dumps(score, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
