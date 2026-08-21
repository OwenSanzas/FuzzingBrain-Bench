"""The fb-bench subcommands: list, show, grade, run, traj, report, models."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fbbench.cli.console import bold, cyan, dim, green, red, yellow
from fbbench.env import detect_provider, read_dotenv
from fbbench.grading import find_bug, grade_blob, list_bugs, read_bench
from fbbench.grading.bench_yaml import harness_sanitizer
from fbbench.models import (
    CATALOG, PRICES, PROVIDER_DEFAULT, PROVIDER_KEY_ENV, needs_key,
    route_provider,
)
from fbbench.paths import REPO


def _require_bug(bug_id: str) -> Path:
    bd = find_bug(bug_id)
    if bd is None:
        sys.exit(red(f"error: bug {bug_id!r} not found"))
    return bd


def cmd_list(_args) -> int:
    bugs = list_bugs()
    print(bold(f"\n  {len(bugs)} bugs available\n"))
    # language + sanitizer: the public build facts setup() already hands the
    # model, so listing them reveals nothing the challenge itself withholds.
    print(f"  {'bug_id':<38s}  {'language':<10s}  {'sanitizer':<12s}  project")
    print(f"  {'-'*38}  {'-'*10}  {'-'*12}  -----")
    for bug_id, bd in bugs:
        try:
            bench = read_bench(bd / "bench.yaml")
            project = bench.get("project", "")
            language = bench.get("language", "")
            san = harness_sanitizer(bd) or ""
        except Exception:
            project, language, san = "", "", ""
        print(f"  {bug_id:<38s}  {language:<10s}  "
              f"{cyan(san):<{12 + len(cyan(san)) - len(san)}}  {dim(project)}")
    print()
    return 0


def cmd_show(args) -> int:
    bd = _require_bug(args.bug_id)
    bench = read_bench(bd / "bench.yaml")

    print()
    print(bold(f"  {args.bug_id}"))
    print(dim(f"  {bench.get('upstream_report', '')}"))
    print()
    print(f"  {'bug_id':<18s} {bench.get('bug_id')}")
    print(f"  {'project':<18s} {bench.get('project')}")
    print(f"  {'language':<18s} {bench.get('language')}")
    print(f"  {'sanitizer':<18s} {cyan(str(harness_sanitizer(bd)))}")
    print()
    desc = bd / "description.txt"
    if desc.exists():
        for line in desc.read_text().splitlines():
            print(f"  {line}")
        print()
    return 0


def cmd_grade(args) -> int:
    """Run one blob through a challenge's harness, in that challenge's image.

    No LLM and no network: the sanitizer harness travels in the image, so this
    is the same code path an episode's run_poc_on_harness takes. What it can
    report is what the harness did — whether the input faulted, and how that
    crash signs. There is no PASS/FAIL against a target defect, because no image
    carries the answer key that question needs.

    Exit 0 when the input crashed the harness, 1 when it did not, so the command
    composes in a shell loop over a fuzzer's findings.
    """
    bd = _require_bug(args.bug_id)
    if not args.blob:
        sys.exit(red("  grade needs a blob: ./fb-bench grade <bug> <input-file>"))
    blob = Path(args.blob)
    if not blob.is_file():
        sys.exit(red(f"error: blob not found: {blob}"))

    print()
    print(bold("  fb-bench grade  ") + cyan(args.bug_id))
    print(f"  {'blob:':<10s} {cyan(str(blob))}  {dim(f'({blob.stat().st_size} bytes)')}")
    print(dim("  running it through the harness in the challenge image…"))

    try:
        r, elapsed = grade_blob(bd, blob)
    except Exception as e:
        sys.exit(red(f"  grade failed: {e}"))

    crashed = bool(r.get("crashed"))
    print()
    if crashed:
        print(bold("  crash:"))
        print(f"    {'class:':<12s} {cyan(r.get('klass') or '—')}")
        if r.get("signature_text"):
            print(f"    {'signature:':<12s} {r['signature_text']}")
        elif r.get("signature"):
            print(f"    {'signature:':<12s} {r['signature']}")

    # The operator must see at least what the model would have seen — the raw
    # harness output of this input. (Server-truncated already: stdout tail 2000,
    # stderr tail 8000.) -v prints both streams whole; without it, stderr alone,
    # which is where every sanitizer report goes.
    print(bold("  harness output:")
          + dim(f"   exit_code={r.get('exit_code')}  signal={r.get('signal') or '—'}"
                f"  {r.get('duration_ms')}ms"))
    printed = False
    for stream in ("stdout", "stderr") if args.verbose else ("stderr",):
        text = (r.get(stream) or "").rstrip("\n")
        if text:
            printed = True
            print(f"    {dim(stream + ':')}")
            for line in text.splitlines():
                print(f"      {line}")
    # A signal death with no captured output means the harness crashed before
    # flushing anything (e.g. a spurious startup segfault) — say so, so a blank
    # block doesn't read as lost/hidden output.
    if not printed and r.get("signal"):
        print(dim("    (no output — harness died on the signal before emitting any)"))

    summary_color = green if crashed else red
    badge = "CRASHED" if crashed else "no crash"
    print()
    print(f"  {bold('verdict:')}   {summary_color(badge)}   {dim(f'{elapsed:.1f}s')}")
    print()
    return 0 if crashed else 1


def cmd_models(_args) -> int:
    env_combined = {**read_dotenv(), **os.environ}
    have = {p: bool(env_combined.get(k)) for p, k in PROVIDER_KEY_ENV.items()}

    print()
    print(bold(f"  fb-bench models  — {len(CATALOG)} supported"))
    print()
    print(f"  {'model':<26s} {'provider':<10s} {'tier':<9s} "
          f"{'in $/M':>7s} {'out $/M':>8s}  key?  default")
    print(dim(f"  {'-'*26} {'-'*10} {'-'*9} {'-'*7} {'-'*8}  ----  -------"))
    for m, prov, tier in CATALOG:
        rate = PRICES.get(m)
        ins = f"{rate[0]:.2f}" if rate else "?"
        outs = f"{rate[1]:.2f}" if rate else "?"
        if not needs_key(prov):
            keyc = cyan("local")
        else:
            keyc = green("yes") if have[prov] else red("no ")
        is_default = cyan(" ✓") if PROVIDER_DEFAULT[prov] == m else ""
        print(f"  {m:<26s} {prov:<10s} {tier:<9s} "
              f"{ins:>7s} {outs:>8s}  {keyc}   {is_default}")
    print()
    print(dim("  `./fb-bench run <bug>` (no --model) auto-picks a default "
              "for the provider whose key you have."))
    print(dim("  prices = USD per 1M tokens (input / output, list rate)."))
    print()
    return 0


def cmd_run(args) -> int:
    """Run an LLM agent through one OR many challenges — one entry, one path.

    A single run is just a 1-cell matrix; N bugs/models/samples is a sweep. Both
    go through the SAME engine (orchestrator.run_matrix). Always pulls the public
    challenge image, which grades inside itself with no network at all.
    """
    from fbbench.sweep.orchestrator import run_matrix, resolve_models, resolve_bugs

    env_combined = {**read_dotenv(), **os.environ}
    arm = getattr(args, "arm", "api")
    auth = getattr(args, "auth", None)   # None => auto (prefer api, else sub)
    model_map: dict[str, str] | None = None
    api_key = args.api_key

    # ---- resolve model(s) per arm. The cell label (dir name) is what goes in
    # `models`; model_map recovers the raw model id an arm needs to execute. ---
    if arm == "codex":
        from fbbench.sweep import codex
        raw_models = ([m.strip() for m in args.model.split(",") if m.strip()]
                      if args.model else [codex.MODEL_DEFAULT])
        models = [codex.model_label(m) for m in raw_models]
        model_map = {codex.model_label(m): m for m in raw_models}
    elif arm == "claudecode":
        from fbbench.sweep import claudecode
        raw_models = ([m.strip() for m in args.model.split(",") if m.strip()]
                      if args.model else [claudecode.MODEL_DEFAULT])
        models = [claudecode.model_label(m) for m in raw_models]
        model_map = {claudecode.model_label(m): m for m in raw_models}
    else:  # api arm — a provider model driven via its API
        if args.model is None:
            provider, have = detect_provider()
            if provider is None:
                sys.exit(red(
                    "  no provider API key found.\n"
                    "  put one into ./.env (or export it):\n"
                    "    ANTHROPIC_API_KEY=sk-ant-...   # claude-* models\n"
                    "    OPENAI_API_KEY=sk-...          # gpt-* models\n"
                    "    GEMINI_API_KEY=...             # gemini-* models\n"
                    "    DEEPSEEK_API_KEY=sk-...        # deepseek-* models\n"
                    "  see `./fb-bench models` for the full list."))
            models = [PROVIDER_DEFAULT[provider]]
            print(dim(f"  no --model given; using {models[0]} "
                      f"(detected {PROVIDER_KEY_ENV[provider]} in .env)"))
        else:
            models = resolve_models(args.model)
            # Validate the key only for the common single-concrete-model case; a
            # lineup (sweep/all/csv) lets each cell surface its own missing-key error.
            if len(models) == 1:
                provider = route_provider(models[0])
                if provider == "unknown":
                    sys.exit(red(f"  cannot route model {models[0]!r} to a provider "
                                 "(expected claude*/gpt*/gemini*)"))
                if (needs_key(provider) and not args.api_key
                        and not env_combined.get(PROVIDER_KEY_ENV[provider])):
                    sys.exit(red(
                        f"  model {models[0]!r} needs ${PROVIDER_KEY_ENV[provider]} "
                        f"but it is not set in ./.env or env.\n"
                        f"  add it to ./.env or pass --api-key."))

    # ---- resolve auth for the vendor arms: prefer api (the provider API key),
    # fall back to sub. Explicit --auth is honoured. ---
    if arm in ("codex", "claudecode"):
        key_env = "OPENAI_API_KEY" if arm == "codex" else "ANTHROPIC_API_KEY"
        have_key = bool(api_key or env_combined.get(key_env))
        if auth is None:
            auth = "api" if have_key else "sub"
            reason = f"{key_env} present" if have_key else f"no {key_env} → subscription sign-in"
            print(dim(f"  --auth not set → {auth} ({reason})"))
        if auth == "api":
            api_key = api_key or env_combined.get(key_env)
            # claudecode authenticates ONLY via the env key, so it must be present.
            # codex can also use a `codex login --with-api-key` auth.json, so a
            # missing env key there is not fatal — it falls through to that login.
            if arm == "claudecode" and not api_key:
                sys.exit(red("  --arm claudecode --auth api needs ANTHROPIC_API_KEY "
                             "in ./.env or --api-key (or use --auth sub)"))

    # ---- resolve bug(s): one | csv | all (validates, exits on unknown) ----
    # argv arrives pre-split when the shell saw whitespace ("a, b" -> ["a,", "b"]);
    # rejoining on commas makes spacing around the separators irrelevant, since
    # resolve_bugs strips each fragment and drops the empties.
    bugs = resolve_bugs(",".join(args.bugs))

    # The runner subprocess runs in whatever interpreter has the deps: a dev
    # checkout's .venv if present, else the current interpreter (pip-installed).
    venv_py = REPO / ".venv" / "bin" / "python"
    runner_py = str(venv_py) if venv_py.is_file() else sys.executable

    return run_matrix(
        models, bugs,
        samples=args.samples, output=args.output,
        max_turns=args.max_turns, timeout=args.timeout, jobs=args.jobs,
        dashboard_pref=getattr(args, "dashboard", None),
        preserve_pocs=args.preserve_pocs,
        stop_on_crash=getattr(args, "stop_on_crash", False),
        api_key=api_key,
        image_prefix=getattr(args, "image_prefix", None),
        report_only=getattr(args, "report_only", False),
        runner=[runner_py, "-m", "fbbench.runner"],
        arm=arm, auth=auth, model_map=model_map,
    )


def cmd_report(args) -> int:
    """(Re)generate report.html for a run dir, or index.html for a sweep/exp dir."""
    from fbbench.runner.report import write_report

    d = Path(args.run_dir)
    if d.is_file():
        d = d.parent
    if (d / "score.json").is_file():
        out = write_report(d)
        print(green(f"  wrote {out}"))
        return 0
    # No score.json here: treat it as a sweep/exp dir and build the summary.
    from fbbench.report import write_summary
    has_cells = any((sub / "score.json").is_file()
                    for bug in d.glob("*") if bug.is_dir()
                    for model in bug.glob("*") if model.is_dir()
                    for sub in model.glob("seed-*"))
    if not has_cells:
        print(red(f"  no score.json (run) or cell tree (sweep) under {d}"), file=sys.stderr)
        return 1
    out = write_summary(d)
    print(green(f"  wrote {out}"))
    return 0


def cmd_traj(args) -> int:
    """Pretty-print the tool-call trajectory of a finished run dir."""
    from fbbench.runner.traj import build_traj, write_traj

    d = Path(args.run_dir)
    tr = d / "transcript.jsonl"
    if not tr.is_file():
        if d.is_file() and d.name == "transcript.jsonl":
            tr, d = d, d.parent
        else:
            print(red(f"  no transcript.jsonl under {d}"), file=sys.stderr)
            return 1
    nodes = build_traj(tr)
    if args.write:
        write_traj(tr, d)
    from fbbench.runner.traj import GRADE_TOOLS
    grades = [n for n in nodes if n["tool"] in GRADE_TOOLS]
    hits = [n for n in grades if n["crash"]]
    print()
    print(bold(f"  {len(nodes)} tool calls · {len(grades)} run_poc_on_harness() · "
               + (green(f"{len(hits)} faulted") if hits else dim("0 faulted"))))
    print()
    for n in nodes:
        head = f"  {n['n']:>3} t{n['turn']:<3} {n['tool']:<14} {n['arg']:<42}"
        if n["crash"]:
            print(green(head) + "  " + green(n["out"]) + "  " + green("💥"))
        elif not n["ok"]:
            print(head + "  " + red(n["out"]))
        else:
            print(head + "  " + dim(n["out"]))
    print()
    return 0
