"""Argument parsing + dispatch for the `fb-bench` CLI."""
from __future__ import annotations

import argparse
import sys

from fbbench.cli import commands
from fbbench.images import DEFAULT_IMAGE_PREFIX


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fb-bench",
        description="FuzzingBrain Bench CLI — drive agents through real-bug challenges.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list every available bug").set_defaults(fn=commands.cmd_list)

    sp_show = sub.add_parser("show", help="show a bug's description")
    sp_show.add_argument("bug_id")
    sp_show.set_defaults(fn=commands.cmd_show)

    sp_grade = sub.add_parser("grade",
                              help="run an input file through a bug's harness, in its image (no LLM)")
    sp_grade.add_argument("bug_id")
    sp_grade.add_argument("blob", help="path to the input file to grade")
    sp_grade.add_argument("-v", "--verbose", action="store_true",
                          help="print the harness stdout as well as stderr")
    sp_grade.set_defaults(fn=commands.cmd_grade)

    sp_run = sub.add_parser("run", help="run an LLM agent through one or many challenges")
    sp_run.add_argument("bugs", metavar="bugs", nargs="+",
                        help="which challenge(s): a single alias (avro-03), a comma "
                             "list (avro-03,jq-01), or 'all'. Whitespace around the "
                             "commas is fine, quoted or not")
    sp_run.add_argument("--arm", choices=("api", "codex", "claudecode", "external"),
                        default="api",
                        help="which agent backend drives the challenge (default: api). "
                             "'api' = a provider model via its API; 'codex' = OpenAI's "
                             "codex CLI; 'claudecode' = the Claude Code CLI; 'external' = "
                             "any agent described by --agent <manifest>")
    sp_run.add_argument("--agent", default=None, metavar="MANIFEST",
                        help="path to an external agent manifest (YAML/JSON). Implies "
                             "--arm external; the agent's code lives in its own repo, "
                             "this file only says how to invoke it")
    sp_run.add_argument("--model", default=None,
                        help="which model(s): one id, a comma list, 'default-lineup' "
                             "(the curated cross-model roster), or 'all'. Default: "
                             "auto-pick from the provider key in .env. "
                             "For --arm codex it sets the codex model (default gpt-5.5); "
                             "for --arm claudecode it picks the claude model")
    sp_run.add_argument("--auth", choices=("api", "sub"), default=None,
                        help="[--arm codex/claudecode] which credential the vendor CLI "
                             "uses: 'api' = the provider API key (pay-go, no throttle), "
                             "'sub' = subscription sign-in (codex: ChatGPT plan; "
                             "claudecode: claude.ai OAuth). Default: auto — prefer api "
                             "when the API key is present, else fall back to sub")
    sp_run.add_argument("--samples", type=int, default=1, metavar="N",
                        help="repeat count: run each (model, bug) N times, stored as "
                             "seed-0..seed-(N-1) (default 1)")
    sp_run.add_argument("--max-turns", type=int, default=100,
                        help="turn budget per episode (default 100)")
    sp_run.add_argument("--timeout", type=int, default=1800,
                        help="per-episode wall-clock seconds (default 1800)")
    sp_run.add_argument("--jobs", "-j", type=int, default=1,
                        help="run N cells concurrently (default 1). 4-6 is usually the "
                             "sweet spot before model rate limits kick in")
    sp_run.add_argument("--output", "-o", default=None,
                        help="where results land. Default: an auto-named folder "
                             "output/run_<timestamp>. A bare name nests under output/ "
                             "(paper-v1 -> output/paper-v1); a path is used as-is. If the "
                             "target already exists a fresh run forks <name>_<timestamp> "
                             "so runs never share a folder. Cells: <output>/<bug>/<model>/seed-N/")
    sp_run.add_argument("--report-only", action="store_true",
                        help="skip running; just re-aggregate the leaderboard from <output>/")
    sp_run.add_argument("--preserve-pocs", action=argparse.BooleanOptionalAction, default=True,
                        help="save every graded blob into <out>/pocs/{crashed,clean}/ "
                             "(default on; --no-preserve-pocs to disable)")
    sp_run.add_argument("--stop-on-crash", action=argparse.BooleanOptionalAction, default=False,
                        help="end at the first crash (default OFF, so the agent "
                             "keeps hunting for more distinct crashes until it stops "
                             "or --max-turns)")
    dash = sp_run.add_mutually_exclusive_group()
    dash.add_argument("--dashboard", dest="dashboard", action="store_true", default=None,
                      help="force the live full-screen dashboard (default: on for a TTY, "
                           "single-job runs)")
    dash.add_argument("--no-dashboard", dest="dashboard", action="store_false",
                      help="disable the live dashboard; line-by-line logs instead")
    sp_run.add_argument("--api-key", default=None,
                        help="provider API key; default reads ./.env")
    sp_run.add_argument("--image-prefix", default=DEFAULT_IMAGE_PREFIX,
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

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
