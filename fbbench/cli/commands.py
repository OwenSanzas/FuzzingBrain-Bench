"""The six fb-bench subcommands: list, show, grade, grade-all, run, models."""
from __future__ import annotations

import datetime
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
    CATALOG, PRICES, PROVIDER_DEFAULT, PROVIDER_KEY_ENV, route_provider,
)
from fbbench.paths import REPO, SERVER

# Reference PoCs that are slow to grade (long harness / heavy build); skipped
# by `grade-all` unless --include-slow is passed.
SLOW_BUGS = {
    "openssl-des-ofb-cfb-overread",
    "imagemagick-msl-comment-npd",
    "ghidra-cplus-demangle-oom",
    "jq-dump-op-npd",
    "icu-translit-rule-uaf",
}


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
    if not SERVER.exists():
        sys.exit(red(f"error: {SERVER} not present. run `make mcp-server`"))
    bd = _require_bug(args.bug_id)
    blob = Path(args.blob) if args.blob else bd / "poc" / "poc.bin"
    if not blob.is_file():
        sys.exit(red(f"error: blob not found: {blob}"))

    K_b = capability_set(bd)
    is_self = args.blob is None
    label = dim("(self-test, bug's own poc.bin)") if is_self else cyan(str(blob))

    print()
    print(bold("  fb-bench grade  ") + cyan(args.bug_id))
    print(f"  {'blob:':<10s} {label}  {dim(f'({blob.stat().st_size} bytes)')}")
    print(f"  {'rounds:':<10s} {args.rounds}")
    print(f"  {'K_b:':<10s} {','.join(K_b)}")
    print(dim(f"  running {args.rounds} randomized rounds (~timeout 30s each)…"))

    try:
        r, elapsed = grade_blob(bd, blob, args.rounds)
    except subprocess.TimeoutExpired:
        sys.exit(red("  grade timed out (300s)"))
    except Exception as e:
        sys.exit(red(f"  grade failed: {e}"))

    caps = r["capabilities"]
    print()
    print(bold("  results:"))
    for flag, tier in TIERS:
        status = caps.get(flag, "n/a")
        glyph, word = fmt_status(status, flag in K_b)
        print(f"    {glyph}  {tier}  {flag:<6s}  {word}")

    if args.verbose:
        ev = r.get("evidence") or {}
        print()
        print(bold("  evidence:"))
        for flag in (f for f, _ in TIERS):
            if ev.get(flag):
                print(f"    {dim(flag + ':'):<10s} {ev[flag]}")

    agreed = r.get("agreed", False)
    kb_ok = all(caps.get(c) == "fired" for c in K_b) and agreed
    summary_color = green if kb_ok else red
    badge = "PASS" if kb_ok else "FAIL"

    print()
    print(f"  {bold('verdict:')}   {summary_color(badge)}   "
          f"{dim(f'agreed={agreed}, {elapsed:.1f}s')}")
    print()
    return 0 if kb_ok else 1


def cmd_grade_all(args) -> int:
    if not SERVER.exists():
        sys.exit(red(f"error: {SERVER} not present. run `make mcp-server`"))
    bugs = list_bugs()
    if not args.include_slow:
        skipped = sorted(b for b, _ in bugs if b in SLOW_BUGS)
        bugs = [(b, d) for b, d in bugs if b not in SLOW_BUGS]
    else:
        skipped = []

    print()
    print(bold(f"  fb-bench grade-all  — {len(bugs)} bugs"))
    if skipped:
        print(dim(f"  skipping {len(skipped)} slow bugs (use --include-slow): {', '.join(skipped)}"))
    print()
    print(f"  {dim('verdict'):<7s}  {'bug':<38s}  fired                             elapsed")
    print(dim(f"  {'-'*7}  {'-'*38}  {'-'*32}  -------"))

    rows: list[tuple[str, str]] = []
    total_t0 = time.time()
    for bug_id, bd in bugs:
        K_b = capability_set(bd)
        blob = bd / "poc" / "poc.bin"
        if not blob.is_file():
            print(f"  {yellow('SKIP'):<7s}  {bug_id:<38s}  {dim('no poc.bin')}")
            rows.append((bug_id, "SKIP"))
            continue
        try:
            r, elapsed = grade_blob(bd, blob, args.rounds)
            caps = r["capabilities"]
            kb_ok = all(caps.get(c) == "fired" for c in K_b) and r.get("agreed", False)
            verdict = "PASS" if kb_ok else "FAIL"
        except Exception:
            verdict, caps, elapsed = "ERR", {}, 0.0

        glyphs = " ".join(
            fmt_status(caps.get(f, "n/a"), f in K_b)[0] + dim(t)
            for f, t in TIERS
        )
        verdict_col = green(verdict) if verdict == "PASS" else red(verdict)
        print(f"  {verdict_col}    {bug_id:<38s}  {glyphs}     {elapsed:5.1f}s")
        rows.append((bug_id, verdict))

    n_pass = sum(1 for _, v in rows if v == "PASS")
    n_fail = sum(1 for _, v in rows if v == "FAIL")
    n_err = sum(1 for _, v in rows if v == "ERR")
    n_skip = sum(1 for _, v in rows if v == "SKIP")
    total = time.time() - total_t0

    print()
    print(bold("  summary:"))
    print(f"    {green('PASS'):<6s} {n_pass:>3d}")
    if n_fail: print(f"    {red('FAIL'):<6s} {n_fail:>3d}")
    if n_err:  print(f"    {red('ERR'):<6s}  {n_err:>3d}")
    if n_skip: print(f"    {yellow('SKIP'):<6s} {n_skip:>3d}")
    print(f"    {dim('total'):<6s} {total:>3.0f}s")
    print()
    return 0 if (n_fail == 0 and n_err == 0) else 1


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
    """Drive an LLM agent through one bug. Wraps `python -m fbbench.runner`.

    Auto-builds bin/mcp-server, provisions .venv on first use, loads the
    provider API key from .env, and — if --model is omitted — picks a sane
    default model based on which provider's key you have.
    """
    _require_bug(args.bug_id)  # validate bug exists before any setup work

    env_combined = {**read_dotenv(), **os.environ}

    if args.model is None:
        provider, have = detect_provider()
        if provider is None:
            sys.exit(red(
                "  no provider API key found.\n"
                "  put one into ./.env (or export it):\n"
                "    ANTHROPIC_API_KEY=sk-ant-...   # claude-* models\n"
                "    OPENAI_API_KEY=sk-...          # gpt-* models\n"
                "    GEMINI_API_KEY=...             # gemini-* models\n"
                "  see `./fb-bench models` for the full list."))
        model = PROVIDER_DEFAULT[provider]
        print(dim(f"  no --model given; using {model} "
                  f"(detected {PROVIDER_KEY_ENV[provider]} in .env)"))
        if len(have) > 1:
            others = ", ".join(PROVIDER_DEFAULT[p] for p in have if p != provider)
            print(dim(f"  other providers available too: {others}"))
    else:
        model = args.model
        provider = route_provider(model)
        if provider == "unknown":
            sys.exit(red(f"  cannot route model {model!r} to a provider "
                         "(expected claude*/gpt*/gemini*)"))
        if not args.api_key and not env_combined.get(PROVIDER_KEY_ENV[provider]):
            sys.exit(red(
                f"  model {model!r} needs ${PROVIDER_KEY_ENV[provider]} "
                f"but it is not set in ./.env or env.\n"
                f"  add it to ./.env or pass --api-key."))

    # ---- build + venv -----------------------------------------------------
    if not SERVER.exists():
        print(dim("  bin/mcp-server missing — building (requires go ≥ 1.22)…"))
        if subprocess.call(["make", "mcp-server"], cwd=str(REPO)) != 0:
            sys.exit(red("  build failed; run `make mcp-server` manually"))

    venv_py = REPO / ".venv" / "bin" / "python"
    if not venv_py.is_file():
        print(dim("  .venv missing — running `make setup` (one-time)…"))
        if subprocess.call(["make", "setup"], cwd=str(REPO)) != 0:
            sys.exit(red("  setup failed; run `make setup` manually"))

    # ---- pick output dir --------------------------------------------------
    if args.output:
        out_dir = Path(args.output)
        exp_label = "(explicit --output)"
    else:
        if args.exp:
            exp = args.exp
            exp_label = f"--exp {exp}"
        else:
            exp = "exp-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            exp_label = "auto-assigned (no --exp given)"
        base = REPO / "runs" / exp / args.bug_id / model
        base.mkdir(parents=True, exist_ok=True)
        n = 0
        while (base / f"run-{n}").exists():
            n += 1
        out_dir = base / f"run-{n}"

    # ---- invoke runner ----------------------------------------------------
    cmd = [str(venv_py), "-m", "fbbench.runner",
           "--bug", args.bug_id,
           "--model", model,
           "--max-turns", str(args.max_turns),
           "--out-dir", str(out_dir)]
    if args.api_key:
        cmd += ["--api-key", args.api_key]
    if args.preserve_pocs:
        cmd.append("--preserve-pocs")

    print()
    print(bold("  fb-bench run  ") + cyan(args.bug_id) +
          dim(f"  model={model}  max-turns={args.max_turns}"))
    print(dim(f"  exp:       {exp_label}"))
    print(dim(f"  output:    {out_dir}"))
    print()
    return subprocess.call(cmd, cwd=str(REPO))
