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
    sp_grade.add_argument("--rounds", type=int, default=3,
                          help="randomization rounds for unanimity (default 3)")
    sp_grade.add_argument("-v", "--verbose", action="store_true",
                          help="print oracle evidence")
    sp_grade.set_defaults(fn=commands.cmd_grade)

    sp_run = sub.add_parser("run", help="drive an LLM agent through one bug (one-liner)")
    sp_run.add_argument("bug_id")
    sp_run.add_argument("--model", default=None,
                        help="model id (default: auto-pick from provider key in .env)")
    sp_run.add_argument("--max-turns", type=int, default=300,
                        help="turn budget (default: 300, matches ExploitBench)")
    sp_run.add_argument("--exp", "-e", default=None,
                        help="experiment namespace (default: auto-assigned exp-<timestamp>); "
                             "groups runs into runs/<exp>/<bug>/<model>/run-N/")
    sp_run.add_argument("--output", "-o", default=None,
                        help="literal output dir; overrides --exp")
    sp_run.add_argument("--preserve-pocs", action="store_true",
                        help="save every graded blob into <out>/pocs/{solved,failed}/")
    sp_run.add_argument("--api-key", default=None,
                        help="provider API key; default reads ./.env")
    sp_run.set_defaults(fn=commands.cmd_run)

    sub.add_parser("models",
                   help="list supported models + show which provider keys are loaded"
                   ).set_defaults(fn=commands.cmd_models)

    sp_all = sub.add_parser("grade-all",
                            help="grade every bug's reference poc (smoke test for the install)")
    sp_all.add_argument("--rounds", type=int, default=3)
    sp_all.add_argument("--include-slow", action="store_true",
                        help="also run the 5 slow bugs (openssl/imagemagick/icu/jq/ghidra-cplus)")
    sp_all.set_defaults(fn=commands.cmd_grade_all)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
