"""The fb-bench subcommands: list, show, grade, grade-all, run, traj, models."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from fbbench.cli.console import (
    TIERS, bold, cyan, dim, fmt_status, green, red, yellow,
)
from fbbench.env import detect_provider, read_dotenv
from fbbench.grading import (
    capability_set, find_bug, grade_blob, list_bugs, read_bench,
)
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
    print(f"  {'bug_id':<38s}  {'K_b':<28s}  title")
    print(f"  {'-'*38}  {'-'*28}  -----")
    for bug_id, bd in bugs:
        try:
            bench = read_bench(bd / "bench.yaml")
            title = bench.get("title", "")
            K_b = bench.get("capability_set", [])
        except Exception:
            title, K_b = "", []
        flags = ",".join(K_b) if K_b else "?"
        print(f"  {bug_id:<38s}  {cyan(flags):<{28 + len(cyan(flags)) - len(flags)}}  {dim(title)}")
    print()
    return 0


def cmd_show(args) -> int:
    bd = _require_bug(args.bug_id)
    bench = read_bench(bd / "bench.yaml")

    print()
    print(bold(f"  {bench.get('title', args.bug_id)}"))
    print(dim(f"  {bench.get('upstream_report', '')}"))
    print()
    print(f"  {'bug_id':<18s} {bench.get('bug_id')}")
    print(f"  {'project':<18s} {bench.get('project')}")
    print(f"  {'capability_set':<18s} {cyan(str(bench.get('capability_set')))}")
    print()
    desc = bd / "description.txt"
    if desc.exists():
        for line in desc.read_text().splitlines():
            print(f"  {line}")
        print()
    return 0


def cmd_grade(args) -> int:
    # No LLM, no Docker: POST the blob to the remote oracle and print the verdict.
    bd = _require_bug(args.bug_id)
    if not args.blob:
        sys.exit(red("  grade needs a blob: ./fb-bench grade <bug> <input-file>"))
    blob = Path(args.blob)
    if not blob.is_file():
        sys.exit(red(f"error: blob not found: {blob}"))

    K_b = capability_set(bd)
    print()
    print(bold("  fb-bench grade  ") + cyan(args.bug_id))
    print(f"  {'blob:':<10s} {cyan(str(blob))}  {dim(f'({blob.stat().st_size} bytes)')}")
    print(f"  {'K_b:':<10s} {','.join(K_b)}")
    print(dim("  POSTing to the remote oracle…"))

    try:
        r, elapsed = grade_blob(bd, blob)
    except Exception as e:
        sys.exit(red(f"  grade failed: {e}"))

    caps = r["capabilities"]
    caps_bestof = r.get("capabilities_bestof") or {}
    print()
    print(bold("  results:") + dim("  (unanimity — fired on every round)"))
    for flag, tier in TIERS:
        status = caps.get(flag, "n/a")
        glyph, word = fmt_status(status, flag in K_b)
        print(f"    {glyph}  {tier}  {flag:<6s}  {word}")

    # Best-of view alongside unanimity (a rung fired on ANY round). Human-facing
    # only; the model never receives either verdict.
    if caps_bestof:
        print()
        print(bold("  results:") + dim("  (best-of — fired on any round)"))
        for flag, tier in TIERS:
            status = caps_bestof.get(flag, "n/a")
            glyph, word = fmt_status(status, flag in K_b)
            print(f"    {glyph}  {tier}  {flag:<6s}  {word}")

    # The human grader must see at least what the model saw — the raw harness
    # output of its own input — plus the verdict on top. (Server-truncated
    # already: stdout tail 2000, stderr tail 8000.)
    ho = r.get("harness_output") or {}
    if ho:
        print()
        print(bold("  harness output:")
              + dim(f"   exit_code={ho.get('exit_code')}  signal={ho.get('signal') or '—'}"))
        printed = False
        for stream in ("stdout", "stderr"):
            text = (ho.get(stream) or "").rstrip("\n")
            if text:
                printed = True
                print(f"    {dim(stream + ':')}")
                for line in text.splitlines():
                    print(f"      {line}")
        # A signal death with no captured output means the harness crashed before
        # flushing anything (e.g. a spurious startup segfault) — say so, so a blank
        # block doesn't read as lost/hidden output.
        if not printed and ho.get("signal"):
            print(dim("    (no output — harness died on the signal before emitting any)"))

    if args.verbose:
        ev = r.get("evidence") or {}
        print()
        print(bold("  evidence:"))
        for flag in (f for f, _ in TIERS):
            if ev.get(flag):
                print(f"    {dim(flag + ':'):<10s} {ev[flag]}")

    agreed = r.get("agreed", False)
    # Authoritative: the oracle's target_bug_found (a single input reproduced the
    # full defect). Fall back to caps-all-fired only if the field is absent.
    if "target_bug_found" in r:
        kb_ok = bool(r["target_bug_found"])
    else:
        kb_ok = all(caps.get(c) == "fired" for c in K_b) and agreed
    summary_color = green if kb_ok else red
    badge = "PASS" if kb_ok else "FAIL"

    print()
    print(f"  {bold('verdict:')}   {summary_color(badge)}   "
          f"{dim(f'agreed={agreed}, {elapsed:.1f}s')}")
    print()
    return 0 if kb_ok else 1


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
    challenge image and grades via the remote oracle (no local mode).
    """
    from fbbench.sweep.orchestrator import run_matrix, resolve_models, resolve_bugs

    env_combined = {**read_dotenv(), **os.environ}

    # ---- resolve model(s): one | csv | sweep | all (or auto-detect one) ---
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

    # ---- resolve bug(s): one | csv | all (validates, exits on unknown) ----
    bugs = resolve_bugs(args.bugs)

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
        full_scan=getattr(args, "full_scan", True),
        stop_on_solve=getattr(args, "stop_on_solve", True),
        api_key=args.api_key,
        image_prefix=getattr(args, "image_prefix", None),
        report_only=getattr(args, "report_only", False),
        runner=[runner_py, "-m", "fbbench.runner"],
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
    from fbbench.runner.traj import build_traj, render_text, write_traj

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
    print(bold(f"  {len(nodes)} tool calls · {len(grades)} grade() · "
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
