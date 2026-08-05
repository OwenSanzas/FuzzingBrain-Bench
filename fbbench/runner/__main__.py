"""CLI entrypoint for the episode driver — `python -m fbbench.runner`.

One invocation = one (model, bug) episode written to --out-dir. Most users go
through `fb-bench run` (which wraps this, picks a model from .env, and creates
a unique output dir); the batch sweep also shells out to this entry per cell.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from fbbench.env import load_dotenv
from fbbench.grading.bench_yaml import capability_set, find_bug, read_bench
from fbbench.images import DEFAULT_IMAGE_PREFIX, DEFAULT_IMAGE_TAG
from fbbench.models import CATALOG, PRICES, cost_usd, default_sweep
from fbbench.paths import REPO
from fbbench.runner.backends import make_backend
from fbbench.runner.episode import run_episode
from fbbench.runner.mcp_client import _full_scan_alias


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
                    help="turn budget per episode (default 100)")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="wall-clock seconds per episode; the agent is told its "
                         "remaining time and the episode self-stops at the limit "
                         "(default 1800)")
    ap.add_argument("--output", default="output", help="output root (legacy nesting <output>/<bug>/<model>/)")
    ap.add_argument("--out-dir", default=None,
                    help="literal output dir; takes precedence over --output")
    ap.add_argument("--preserve-pocs", action=argparse.BooleanOptionalAction, default=True,
                    help="save every graded candidate blob into pocs/{solved,failed}/ "
                         "(default on; pass --no-preserve-pocs to disable)")
    ap.add_argument("--stop-on-solve", action=argparse.BooleanOptionalAction, default=False,
                    help="end the episode as soon as the target defect is reproduced "
                         "(default OFF, so the agent keeps hunting for more distinct "
                         "crashes until it stops or --max-turns)")
    # Context mode. full-scan (blind) is the one active public mode — the bug
    # description is withheld and the agent finds the crash from the harness +
    # source alone. diffscan (delta-N) is a reserved extension point, not yet
    # implemented (see prompts.build_diffscan_message).
    ap.add_argument("--batch", default=None,
                    help="uid of the sweep this cell belongs to; the orchestrator "
                         "registers the sweep's flags under it once")
    ap.add_argument("--seed", type=int, default=None,
                    help="which sample of this (bug, model) cell this run is; recorded "
                         "with the run id so repeats can be told apart")
    ap.add_argument("--mode", default="full-scan", choices=("full-scan", "diffscan"),
                    help="agent context mode (default: full-scan / blind)")
    ap.add_argument("--repo-root", default=None,
                    help="benchmark repo root (default: auto-detected)")
    ap.add_argument("--api-key", default=None, help="provider API key (or use the env var)")
    ap.add_argument("--image-prefix", default=DEFAULT_IMAGE_PREFIX,
                    help="registry prefix for the canonical challenge images")
    ap.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG,
                    help=f"tag on the challenge image (default: {DEFAULT_IMAGE_TAG}). "
                         f"'{DEFAULT_IMAGE_TAG}' is the self-contained variant that "
                         "grades inside the image and needs no network; 'latest' "
                         "grades against the remote oracle")
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

    bug_dir = find_bug(args.bug, repo_root)
    if bug_dir is None:
        print(f"error: bug {args.bug} not found under {repo_root}/bugs", file=sys.stderr)
        return 2

    # The agent runs against the PUBLIC challenge image — the same artifact the
    # world runs. Which one depends on the tag: :local-v1 (the default) grades
    # inside the image with no network at all, :latest grades by POSTing to the
    # remote oracle. A bug may pin its own image via the optional top-level
    # `image:` field in bench.yaml (a full ref, e.g.
    # docker.io/zhicheng/my-bug:latest), which carries its own tag and so ignores
    # --image-tag; otherwise fall back to <image_prefix><alias>:<image_tag> under
    # the canonical namespace.
    #
    # The tag is always written out rather than left off, because a bare name is
    # not "whatever is local" — Docker resolves it to :latest, which is the
    # remote-graded image and not what the default asked for.
    bug_image = read_bench(Path(bug_dir) / "bench.yaml").get("image")
    image = bug_image or f"{args.image_prefix}{_full_scan_alias(str(bug_dir))}:{args.image_tag}"
    out_dir = (Path(args.out_dir) if args.out_dir
               else Path(args.output) / args.bug / args.model)
    out_dir.mkdir(parents=True, exist_ok=True)

    backend = make_backend(args.model, api_key=args.api_key)
    pocs_dir = (out_dir / "pocs") if args.preserve_pocs else None
    # One id per episode, so the oracle can group this run's submissions. Minted
    # here rather than per grade: the point is that twenty inputs from one run
    # are one attempt at the challenge, not twenty. Nothing depends on it being
    # unforgeable — it decides no verdict, only how results are grouped.
    run_identity = {
        "uid": uuid.uuid4().hex,
        "batch": args.batch or "",
        "model": args.model,
        "arm": "api",
        "seed": str(args.seed) if args.seed is not None else "",
    }
    # Everything (challenge surface, workspace, remote grading) lives in the
    # image; the host stages nothing. bug_dir="/src" is the in-container view.
    ep_bug_dir = "/src"
    result = run_episode(
        backend=backend,
        bug_id=args.bug,
        bug_dir=ep_bug_dir,
        oracle_dir=str(bug_dir),
        workspace="",
        image=image,
        max_turns=args.max_turns,
        time_budget_s=args.timeout,
        episode_log=str(out_dir / "episode.jsonl"),
        capability_set=capability_set(bug_dir),
        pocs_dir=str(pocs_dir) if pocs_dir else None,
        stop_on_solve=args.stop_on_solve,
        mode=args.mode,
        run=run_identity,
    )

    score = {
        "bug_id": result.bug_id,
        "model": result.model,
        # Identity, not score. The oracle knows which distinct crashes this run
        # found; the runner deliberately does not, because a runner that knows
        # can leak it back into the episode. Recording the id lets the report --
        # which runs afterwards, outside any episode -- ask the oracle.
        "run_uid": run_identity["uid"],
        "batch_uid": run_identity["batch"] or None,
        # Every run knob that shaped this episode — surfaced verbatim in the
        # report so a result is fully reproducible from its own score.json.
        "config": {
            "mode": args.mode,
            "max_turns": args.max_turns,
            "timeout_s": args.timeout,
            "stop_on_solve": bool(args.stop_on_solve),
            "preserve_pocs": bool(args.preserve_pocs),
            # What actually graded this episode, not what we expected to. It was
            # hardcoded to "remote-oracle", which was true of every run until the
            # images could grade themselves and then quietly was not — a run
            # graded entirely offline still reported that an oracle had judged
            # it. "not-exercised" means the model never submitted a candidate, so
            # nothing graded and there is nothing to claim.
            "grading": result.grading or "not-exercised",
            "image": image,
            "capability_set": sorted(capability_set(bug_dir) or []),
        },
        # Headline metric: the number of DISTINCT crashes the agent found (unique
        # crash-type + top-frames signatures). The capability ladder below is kept
        # as a per-oracle diagnostic, not the score.
        "score": result.unique_crashes,
        "unique_crashes": result.unique_crashes,
        "crash_signatures": sorted(result.crash_signatures),
        # Where each of those crashes faulted. NOT part of the identity — the
        # signature is deliberately name-only, because names survive the rebuilds
        # this corpus does constantly and line numbers do not. It is here because
        # grouping crashes into the DEFECTS they point at needs the faulting
        # location, and re-deriving it later from an archived transcript fails
        # exactly where it matters: the deep traces get truncated on the way.
        #
        # This is an OBSERVATION, and the only thing stored. The bug count itself
        # is an interpretation of it (fbbench.grading.bugs) and is derived where
        # it is reported — storing it too would freeze one ruleset into every
        # cell and go stale the moment the rules improve.
        "crash_frames": {s: result.crash_frames.get(s, [])
                         for s in sorted(result.crash_signatures)},
        "terminated_reason": result.terminated_reason,
        "refusal_retries": result.refusal_retries,
        "malformed_retries": result.malformed_retries,
        "turns_used": result.turns_used,
        "duration_s": result.duration_s,
    }
    # The five-rung ladder and `solved` are the ORACLE's verdict. Local grading
    # does not compute them — it has no answer key, no coverage build and no
    # fixed-commit build — so under it every rung sat at "not_fired" and `solved`
    # at false. Both read as measurements that came back negative when nothing
    # was ever measured, which is the more misleading of the two mistakes.
    #
    # So they are written only when a grader actually produced them. A consumer
    # that finds no `capabilities` key knows the run has nothing to say about the
    # ladder, rather than being told it failed every rung.
    if result.grading != "in-image":
        score.update({
            "solved": result.solved,
            "capabilities": result.capabilities,
            "capabilities_bestof": result.capabilities_bestof,
            "tier_score": sum(1 for v in result.capabilities.values() if v == "fired"),
            "tier_score_bestof": sum(1 for v in result.capabilities_bestof.values()
                                     if v == "fired"),
        })
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
