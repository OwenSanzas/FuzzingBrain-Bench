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
from fbbench.grading.bench_yaml import capability_set, find_bug
from fbbench.models import CATALOG, PRICES, cost_usd, default_sweep
from fbbench.paths import REPO
from fbbench.runner.backends import make_backend
from fbbench.runner.episode import run_episode
from fbbench.runner.mcp_client import stage_bug_view


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
    ap.add_argument("--bug", help="bug_id (e.g. netsnmp-vacm-parse-npd)")
    ap.add_argument("--model", default="claude-opus-4-7", help="model id (claude*/gpt*/gemini*)")
    ap.add_argument("--max-turns", type=int, default=300,
                    help="turn budget per episode (default 300, matches ExploitBench v8.yaml)")
    ap.add_argument("--output", default="runs", help="output root (legacy nesting <output>/<bug>/<model>/)")
    ap.add_argument("--out-dir", default=None,
                    help="literal output dir; takes precedence over --output")
    ap.add_argument("--preserve-pocs", action="store_true",
                    help="save every graded candidate blob into pocs/{solved,failed}/")
    ap.add_argument("--server-bin", default=None,
                    help="path to mcp-server binary (default: ./bin/mcp-server)")
    ap.add_argument("--repo-root", default=None,
                    help="benchmark repo root (default: auto-detected)")
    ap.add_argument("--api-key", default=None, help="provider API key (or use the env var)")
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
    if not Path(server_bin).is_file():
        print(f"error: mcp-server binary not found at {server_bin}; build with:", file=sys.stderr)
        print(f"  go -C {repo_root}/tools/mcp-server build -o {server_bin}", file=sys.stderr)
        return 2

    bug_dir = find_bug(args.bug, repo_root)
    if bug_dir is None:
        print(f"error: bug {args.bug} not found under {repo_root}/bugs", file=sys.stderr)
        return 2
    out_dir = (Path(args.out_dir) if args.out_dir
               else Path(args.output) / args.bug / args.model)
    out_dir.mkdir(parents=True, exist_ok=True)

    workspace = tempfile.mkdtemp(prefix=f"fbbench-{args.bug}-")
    # Agent sees a staged sandbox (no grader/, poc/, binaries/); the grader
    # reads the answer key + ground-truth binaries from the real bug dir.
    bug_view = stage_bug_view(str(bug_dir))
    backend = make_backend(args.model, api_key=args.api_key)
    pocs_dir = (out_dir / "pocs") if args.preserve_pocs else None
    try:
        result = run_episode(
            backend=backend,
            bug_id=args.bug,
            bug_dir=bug_view,
            oracle_dir=str(bug_dir),
            workspace=workspace,
            server_bin=server_bin,
            max_turns=args.max_turns,
            episode_log=str(out_dir / "episode.jsonl"),
            capability_set=capability_set(bug_dir),
            pocs_dir=str(pocs_dir) if pocs_dir else None,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(bug_view, ignore_errors=True)

    score = {
        "bug_id": result.bug_id,
        "model": result.model,
        "capabilities": result.capabilities,
        "tier_score": sum(1 for v in result.capabilities.values() if v == "fired"),
        "terminated_reason": result.terminated_reason,
        "refusal_retries": result.refusal_retries,
        "malformed_retries": result.malformed_retries,
        "turns_used": result.turns_used,
        "duration_s": result.duration_s,
    }
    cost = {"model": result.model,
            **cost_usd(result.model, result.input_tokens, result.output_tokens)}
    score["total_usd"] = cost["total_usd"]
    (out_dir / "score.json").write_text(json.dumps(score, indent=2))
    (out_dir / "cost.json").write_text(json.dumps(cost, indent=2))

    print(json.dumps(score, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
