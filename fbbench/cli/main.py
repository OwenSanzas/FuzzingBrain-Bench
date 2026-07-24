"""Argument parsing + dispatch for the `fb-bench` CLI."""
from __future__ import annotations

import argparse
import sys

from fbbench.cli import commands


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fb-bench",
        description="FuzzingBrain Bench CLI — grade blobs against real-bug oracles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list every available bug").set_defaults(fn=commands.cmd_list)

    sp_show = sub.add_parser("show", help="show a bug's description")
    sp_show.add_argument("bug_id")
    sp_show.set_defaults(fn=commands.cmd_show)

    sp_grade = sub.add_parser("grade", help="grade a blob against a bug's oracle")
    sp_grade.add_argument("bug_id")
    sp_grade.add_argument("blob", nargs="?",
                          help="path to blob (default: bug's own poc/poc.bin)")
    sp_grade.add_argument("--rounds", type=int, default=1,
                          help="grade rounds (default 1; the corpus is deterministic). "
                               "Use --rounds 3 as the opt-in determinism gate.")
    sp_grade.add_argument("-v", "--verbose", action="store_true",
                          help="print oracle evidence")
    sp_grade.set_defaults(fn=commands.cmd_grade)

    sp_run = sub.add_parser("run", help="drive an LLM agent through one bug (one-liner)")
    sp_run.add_argument("bug_id")
    sp_run.add_argument("--model", default=None,
                        help="model id (default: auto-pick from provider key in .env)")
    sp_run.add_argument("--max-turns", type=int, default=100,
                        help="turn budget (default: 100 for full-scan; diff-scan uses 50)")
    sp_run.add_argument("--output", "-o", default=None,
                        help="where results land (default: ./output). A bare name nests "
                             "under it (paper-v1 -> output/paper-v1); a path is used as-is. "
                             "Each run gets output/<bug>/<model>/run-N/")
    sp_run.add_argument("--preserve-pocs", action=argparse.BooleanOptionalAction, default=True,
                        help="save every graded blob into <out>/pocs/{solved,failed}/ "
                             "(default on; --no-preserve-pocs to disable)")
    sp_run.add_argument("--stop-on-solve", action=argparse.BooleanOptionalAction, default=True,
                        help="end at the first target solve (default on; "
                             "--no-stop-on-solve lets the agent keep hunting until "
                             "it stops or --max-turns)")
    sp_run.add_argument("--full-scan", action="store_true",
                        help="harder mode: withhold the bug description; the agent "
                             "gets only the harness and must find a crashing input")
    sp_run.add_argument("--api-key", default=None,
                        help="provider API key; default reads ./.env")
    sp_run.add_argument("--local", action="store_true",
                        help="DEV ONLY: drive a host mcp-server graded against the "
                             "local oracle. The default (canonical) path pulls the "
                             "PUBLIC challenge image and grades via the remote oracle "
                             "baked into it — identical to what anyone else runs, so "
                             "scores are reproducible. Needs only Docker, not Go.")
    sp_run.add_argument("--image-prefix", default="docker.io/osanzas/fbbench-challenge-",
                        help="registry prefix for the canonical challenge images")
    sp_run.set_defaults(fn=commands.cmd_run)

    sp_traj = sub.add_parser("traj",
                             help="print the tool-call trajectory of a finished run dir")
    sp_traj.add_argument("run_dir",
                         help="a run/cell dir containing transcript.jsonl")
    sp_traj.add_argument("--write", action="store_true",
                         help="(re)write traj.jsonl + traj.md into the run dir")
    sp_traj.set_defaults(fn=commands.cmd_traj)

    sp_report = sub.add_parser("report",
                               help="(re)generate report.html for a run dir, or index.html for a sweep/exp dir")
    sp_report.add_argument("run_dir",
                           help="a run/cell dir (-> report.html) or a sweep exp dir (-> index.html)")
    sp_report.set_defaults(fn=commands.cmd_report)

    sub.add_parser("models",
                   help="list supported models + show which provider keys are loaded"
                   ).set_defaults(fn=commands.cmd_models)

    sp_all = sub.add_parser("grade-all",
                            help="grade every bug's reference poc (smoke test for the install)")
    sp_all.add_argument("--rounds", type=int, default=1,
                        help="grade rounds (default 1; the corpus is deterministic). "
                             "Use --rounds 3 as the opt-in determinism gate.")
    sp_all.add_argument("--include-slow", action="store_true",
                        help="also run the 4 slow bugs (openssl/imagemagick/icu/jq)")
    sp_all.set_defaults(fn=commands.cmd_grade_all)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
