"""CLI entrypoint for the FuzzingBrain Bench runner.

Usage:
  python -m runner --bug netsnmp-vacm-parse-npd \\
                   --model claude-opus-4-7 \\
                   --seed 0 \\
                   --max-turns 60 \\
                   --output runs/

Each (model, bug, seed) run becomes runs/<bug_id>/<model>/seed-<n>/.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from episode import run_episode  # noqa: E402
from backends import make_backend  # noqa: E402
from mcp_client import stage_bug_view  # noqa: E402


def load_dotenv(repo_root: Path) -> None:
    """Best-effort load of repo .env so provider keys are available."""
    import os
    env = repo_root / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def find_bug_dir(repo_root: Path, bug_id: str) -> Path:
    for sub in (repo_root / "bugs").iterdir():
        if not sub.is_dir():
            continue
        cand = sub / bug_id
        if cand.is_dir():
            return cand
    raise FileNotFoundError(f"bug {bug_id} not found under {repo_root}/bugs")


def main() -> int:
    ap = argparse.ArgumentParser(description="FuzzingBrain Bench runner")
    ap.add_argument("--bug", required=True, help="bug_id (e.g. netsnmp-vacm-parse-npd)")
    ap.add_argument("--model", default="claude-opus-4-7", help="Anthropic model id")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--output", default="runs", help="output directory root")
    ap.add_argument("--server-bin", default=None,
                    help="path to mcp-server binary (default: ./bin/mcp-server)")
    ap.add_argument("--repo-root", default=None,
                    help="benchmark repo root (default: parent of runner/)")
    ap.add_argument("--api-key", default=None, help="Anthropic API key (or use ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root or Path(__file__).resolve().parent.parent)
    load_dotenv(repo_root)
    server_bin = args.server_bin or str(repo_root / "bin" / "mcp-server")
    if not Path(server_bin).is_file():
        print(f"error: mcp-server binary not found at {server_bin}; build with:", file=sys.stderr)
        print(f"  go -C {repo_root}/tools/mcp-server build -o {server_bin}", file=sys.stderr)
        return 2

    bug_dir = find_bug_dir(repo_root, args.bug)
    out_dir = Path(args.output) / args.bug / args.model / f"seed-{args.seed}"
    out_dir.mkdir(parents=True, exist_ok=True)

    workspace = tempfile.mkdtemp(prefix=f"fbbench-{args.bug}-")
    # Agent sees a staged sandbox (no grader/, poc/, binaries/); the grader
    # reads the answer key + ground-truth binaries from the real bug dir.
    bug_view = stage_bug_view(str(bug_dir))
    backend = make_backend(args.model, api_key=args.api_key, seed=args.seed)
    try:
        result = run_episode(
            backend=backend,
            bug_id=args.bug,
            bug_dir=bug_view,
            oracle_dir=str(bug_dir),
            workspace=workspace,
            server_bin=server_bin,
            seed=args.seed,
            max_turns=args.max_turns,
            episode_log=str(out_dir / "episode.jsonl"),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(bug_view, ignore_errors=True)

    score = {
        "bug_id": result.bug_id,
        "model": result.model,
        "seed": result.seed,
        "capabilities": result.capabilities,
        "tier_score": sum(1 for v in result.capabilities.values() if v == "fired"),
        "terminated_reason": result.terminated_reason,
        "refusal_retries": result.refusal_retries,
        "turns_used": result.turns_used,
        "duration_s": result.duration_s,
    }
    (out_dir / "score.json").write_text(json.dumps(score, indent=2))
    cost = {
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
    (out_dir / "cost.json").write_text(json.dumps(cost, indent=2))

    print(json.dumps(score, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
