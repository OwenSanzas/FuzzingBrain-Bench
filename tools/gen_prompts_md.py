#!/usr/bin/env python3
"""Generate docs/PROMPTS.md from fbbench/prompts.py — the single source.

Every prompt is registered in prompts.py with `when` (the situation it is sent
in) and `why` (the business reason); this renders them into a readable catalog so
the team can review all model-facing text in one place. The .md is a generated
VIEW — never hand-edit it; edit prompts.py and re-run:

    PYTHONPATH=. python tools/gen_prompts_md.py        # write docs/PROMPTS.md
    PYTHONPATH=. python tools/gen_prompts_md.py --check # exit 1 if out of date

tests/test_prompts_doc.py runs --check so the doc can never drift.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fbbench.prompts import derived_prompts, registry

_OUT = Path(__file__).resolve().parents[1] / "docs" / "PROMPTS.md"

# The MCP tools are NOT hard-coded here: they are pulled LIVE from a challenge
# image's mcp-server at render time (tools/list), so this doc always reflects the
# real tool schemas the agent receives. Override the image via --image or env.
_DEFAULT_IMAGE = os.environ.get(
    "FBBENCH_IMAGE_PREFIX", "docker.io/osanzas/fbbench-challenge-") + "avro-03"

_HEADER = (
    "# FuzzingBrain-Bench — model-facing prompts\n\n"
    "**Auto-generated from `fbbench/prompts.py` by `tools/gen_prompts_md.py`. "
    "Do NOT edit by hand** — edit `prompts.py` and re-run the generator "
    "(`tests/test_prompts_doc.py` fails if this file is stale).\n\n"
    "Every string the benchmark sends to a model lives in `prompts.py`; each is "
    "listed below with **when** it is used and **why** (the business reason). "
    "Fixed prompts show their full text; dynamic ones show the template with "
    "`{placeholders}` for the per-episode values (description, setup() payload, "
    "file list, turn counts) substituted at runtime. The final **Assembled "
    "prompts** section shows the exact as-sent text for prompts the runner builds "
    "from several fragments, computed from the real builders so it cannot drift.\n"
)


def _render_entry(out: list[str], p) -> None:
    out.append(f"\n## `{p.id}`\n")
    out.append(f"- **When**: {p.when}")
    out.append(f"- **Why**: {p.why}")
    if p.fills:
        out.append(f"- **Type**: dynamic — fills `{p.fills}`")
    else:
        out.append("- **Type**: fixed")
    out.append("\n```\n" + p.text + "\n```\n")


def load_mcp_tools(image: str) -> list[dict]:
    """Pull the live tool schemas from a challenge image's mcp-server (tools/list)
    — the exact set the agent receives. Not hard-coded, so this never drifts from
    the real image. Requires Docker + the image locally."""
    from fbbench.runner.mcp_client import MCPClient  # local import: Docker only here
    m = MCPClient(bug_dir="", workspace="", image=image)
    try:
        m.initialize()
        return m.list_tools()
    finally:
        try:
            m.close()
        except Exception:  # noqa: BLE001
            pass


def _render_tools(out: list[str], tools: list[dict], image: str) -> None:
    out.append("\n---\n")
    out.append("\n# MCP tools (as the agent sees them)\n")
    out.append(
        f"Pulled **live** from `{image}`'s mcp-server (`tools/list`) at render "
        "time — not hard-coded, so this always matches the real image. The system "
        "prompt does NOT enumerate the tools; each reaches the agent ONLY as its "
        "**name + description + input schema**, delivered via the provider's "
        "tool-calling API (serialized into the model's context). So the text below "
        "is the ENTIRE spec the agent has for each tool.\n")
    for t in tools:
        out.append(f"\n## tool: `{t.get('name','?')}`\n")
        out.append(f"- **Description**: {t.get('description','')}")
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if props:
            out.append("- **Parameters**:")
            for pname, pdef in props.items():
                typ = (pdef or {}).get("type", "?")
                req = "required" if pname in required else "optional"
                out.append(f"    - `{pname}` ({typ}, {req})")
        else:
            out.append("- **Parameters**: none")
        out.append("\n```json\n" + json.dumps(t, indent=2, ensure_ascii=False) + "\n```\n")


def render(tools: list[dict], image: str) -> str:
    out = [_HEADER]
    prompts = registry()
    derived = derived_prompts()
    # table of contents
    out.append("\n## Index\n")
    for p in prompts:
        kind = "dynamic" if p.fills else "fixed"
        out.append(f"- [`{p.id}`](#{p.id.replace('.', '').replace('_', '-')}) — {kind}")
    for p in derived:
        out.append(f"- [`{p.id}`](#{p.id.replace('.', '').replace('_', '-')}) — assembled")
    out.append("- [MCP tools](#mcp-tools-as-the-agent-sees-them) — live from the image")
    out.append("\n---\n")
    for p in prompts:
        _render_entry(out, p)
    out.append("\n---\n")
    out.append("\n# Assembled prompts (exact text as sent)\n")
    out.append(
        "These are not single registry strings — the runner builds them from the "
        "fragments above. Shown here as the exact text the model receives, computed "
        "from the builder functions so this section can never drift from runtime.\n")
    for p in derived:
        _render_entry(out, p)
    _render_tools(out, tools, image)
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    image = args[args.index("--image") + 1] if "--image" in args else _DEFAULT_IMAGE

    # MCP tools are pulled LIVE from the image (needs Docker). Fail loudly rather
    # than silently emit a doc with stale/empty tools.
    try:
        tools = load_mcp_tools(image)
    except Exception as e:  # noqa: BLE001
        print(f"error: could not load live MCP tools from {image}: {e}\n"
              f"  (Docker + the image are required; override with --image <ref>)",
              file=sys.stderr)
        return 2

    text = render(tools, image)
    if check:
        cur = _OUT.read_text() if _OUT.exists() else ""
        if cur != text:
            print(f"OUT OF DATE: {_OUT} differs from prompts.py / live tools — "
                  f"run `python tools/gen_prompts_md.py`", file=sys.stderr)
            return 1
        print(f"up to date: {_OUT}")
        return 0
    _OUT.write_text(text)
    print(f"wrote {_OUT} ({len(registry())} prompts, {len(tools)} live MCP tools "
          f"from {image})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
