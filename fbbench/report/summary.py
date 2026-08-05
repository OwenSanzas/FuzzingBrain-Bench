"""Build a self-contained, answer-free sweep summary page.

After a sweep, :func:`write_summary` injects a params+results blob into
``summary_template.html`` and writes ``<output>/index.html`` — a double-clickable
matrix of every (bug x model) cell, each linking to that episode's own report.

ANSWER SAFETY: the summary reads only each cell's ``score.json`` (the agent's
achieved tier + which ladder flags fired + its own crash signatures + cost +
terminated reason). It never opens ``expected.yaml`` / ``poc`` / a description,
and emits no bug class or crash location. "solved" is derived purely from the
cell's own capabilities (every applicable, non-``n/a`` flag fired) — so no
answer key is consulted. Crash signatures are the AGENT's findings, distilled
from harness output it had already seen; they say what it hit, never what it was
supposed to hit.
"""
from __future__ import annotations

import json
from pathlib import Path

from fbbench.grading import pool
from fbbench.grading.bugs import count as bug_count

_TEMPLATE = Path(__file__).with_name("summary_template.html")
_DIFFICULTY = Path(__file__).with_name("difficulty.json")
LADDER = ["reach", "crash", "differential", "class", "site"]


def _load_difficulty() -> tuple[dict, int]:
    """Per-bug difficulty D (1..5) + the max score (sum of D over all 68 bugs).

    D comes from the published N=8 pyramid (D = 5 - ceil(S/2), S = # of the 8
    frontier runs that solved the bug). A model's Score = sum of D over the bugs
    it solved — solving rare hard bugs is worth more. Answer-safe: difficulty is
    an aggregate solve-rate, not any bug's PoC/fault.
    """
    try:
        d = json.loads(_DIFFICULTY.read_text())
        return d.get("difficulty", {}), int(d.get("max_score", 0))
    except Exception:
        return {}, 0


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _solved(sc: dict) -> bool:
    # Authoritative: a single candidate reproduced the full target defect
    # (score.solved). Fall back to the best-candidate caps only for older runs
    # that predate the field. NEVER a sticky union across candidates.
    if "solved" in sc:
        return bool(sc["solved"])
    caps = sc.get("capabilities", {})
    applicable = {k: v for k, v in caps.items() if v != "n/a"}
    return bool(applicable) and all(v == "fired" for v in applicable.values())


def _tag_of(image: str) -> str:
    """The tag half of an image ref — 'local-v1' out of '…/avro-03:local-v1'.

    A ref with no tag is not untagged in practice: Docker resolves a bare name to
    ``:latest``, which is the remote-graded image. Early runs recorded the name
    that way, so say what they actually ran rather than nothing. The last colon
    only starts a tag when no ``/`` follows it, because a registry host may carry
    a port (``localhost:5000/img``).
    """
    _, sep, tail = image.rpartition(":")
    return tail if (sep and "/" not in tail) else "latest (implicit)"


def _scan_dimensions(exp_dir: Path) -> tuple[list[str], list[str], list[int]]:
    """Infer (bugs, models, samples) from the on-disk cell tree."""
    bugs, models, samples = [], set(), set()
    for bug_dir in sorted(p for p in exp_dir.iterdir() if p.is_dir()):
        has_cell = False
        for model_dir in sorted(p for p in bug_dir.iterdir() if p.is_dir()):
            for seed_dir in model_dir.iterdir():
                if seed_dir.name.startswith("seed-") and (seed_dir / "score.json").is_file():
                    has_cell = True
                    models.add(model_dir.name)
                    try:
                        samples.add(int(seed_dir.name.split("-", 1)[1]))
                    except ValueError:
                        pass
        if has_cell:
            bugs.append(bug_dir.name)
    return bugs, sorted(models), sorted(samples)


def build_summary(exp_dir: str | Path, *, exp: str | None = None,
                  models: list[str] | None = None, bugs: list[str] | None = None,
                  samples: list[int] | None = None, max_turns: int = 0,
                  total_cost: float | None = None, elapsed_s: float = 0.0) -> dict:
    exp_dir = Path(exp_dir)
    s_bugs, s_models, s_samples = _scan_dimensions(exp_dir)
    bugs = bugs or s_bugs
    models = models or s_models
    samples = samples if samples is not None else s_samples

    difficulty, max_score = _load_difficulty()

    # Unique crashes come from the oracle: it is the only side that can tell one
    # crash from another, and a runner that could would be able to leak it into
    # the episode. Safe to read HERE — a summary is built after every episode has
    # ended. None when there is no batch id or the oracle is unreachable, which
    # the template renders as absent rather than as zero.
    uid = pool.batch_uid(exp_dir)
    crash_score = pool.batch_score(uid) if uid else None
    crashes_by_cell = pool.per_challenge_crashes(crash_score)
    cells = []
    cost_sum = 0.0
    cfg_seen: dict[str, set] = {}     # config key -> set of values seen across cells
    # Signature -> frames, unioned across a pair's samples and across a
    # challenge's models. Bug counts have to be computed on the UNION, not summed
    # from the cells: two samples that hit the same defect two ways found one
    # bug, and summing per-cell answers would count it twice.
    pair_sigs: dict[tuple[str, str], dict[str, list]] = {}
    challenge_sigs: dict[str, dict[str, list]] = {}
    for bug in bugs:
        for model in models:
            for sample in samples:
                cd = exp_dir / bug / model / f"seed-{sample}"
                sj = cd / "score.json"
                if not sj.is_file():
                    continue
                sc = _load(sj)
                caps = sc.get("capabilities", {})
                cost = float(sc.get("total_usd") or 0.0)
                cost_sum += cost
                cfg = sc.get("config") or {}
                for k, v in cfg.items():
                    if isinstance(v, (list, dict)):
                        continue
                    cfg_seen.setdefault(k, set()).add(v)
                # Derived, and it has to be derived HERE: every bug has its own
                # image, so the full refs never agree — it is the tag they share.
                if cfg.get("image"):
                    cfg_seen.setdefault("image_tag", set()).add(_tag_of(cfg["image"]))
                report = cd / "report.html"
                cell_crashes = crashes_by_cell.get((model, bug))
                # Two different questions, two numbers. `crashes` counts the
                # distinct ways this cell reached a fault; `bugs` counts the
                # defects those faults point at. Recomputed here from the stored
                # rows rather than read from score.json, so a rule change in
                # fbbench.grading.bugs re-derives every past run's answer on the
                # next report build — including runs made before the field
                # existed, which have signatures but no frames.
                sigs = sorted(sc.get("crash_signatures") or [])
                frames = sc.get("crash_frames") or {}
                cell_sigmap = {s: frames.get(s) or [] for s in sigs}
                for s, fr in cell_sigmap.items():
                    pair_sigs.setdefault((bug, model), {}).setdefault(s, fr)
                    challenge_sigs.setdefault(bug, {}).setdefault(s, fr)
                cells.append({
                    "bug": bug, "model": model, "sample": sample,
                    "tier": int(sc.get("tier_score", 0)),
                    # This cell's own count, from its score.json. `uniq_crashes`
                    # below comes from the oracle's pool and is None when there
                    # is no oracle to ask — which is exactly the locally-graded
                    # case, so the two are kept apart rather than merged.
                    "crashes": int(sc.get("unique_crashes", 0)),
                    # How many DEFECTS this cell's crashes point at.
                    "bugs": bug_count(cell_sigmap),
                    # The signatures behind that count (crash type + top frames),
                    # so the page can dedupe across seeds and show WHAT was hit
                    # rather than only how many. The agent's own findings — see
                    # the answer-safety note at the top of this module.
                    "sigs": sigs,
                    # Whether this cell was graded against the capability ladder
                    # at all. False for in-image grading, which has no answer key.
                    "has_ladder": bool(caps),
                    # Distinct crashes this cell produced; None when unknown.
                    "uniq_crashes": cell_crashes["crashes"] if cell_crashes else None,
                    "uniq_unpatched": (cell_crashes["unpatched_upstream"]
                                       if cell_crashes else None),
                    "d": int(difficulty.get(bug, 0)),  # published difficulty 1..5
                    "caps": caps,
                    "solved": _solved(sc),
                    "cost": cost,
                    "mode": cfg.get("mode") or sc.get("mode") or "blind",
                    "reason": sc.get("terminated_reason", ""),
                    "report": (str(report.relative_to(exp_dir)) if report.is_file() else ""),
                })

    # Sweep-level run config: a value if every cell agrees, else "mixed".
    def _agree(key, default=None):
        vals = cfg_seen.get(key)
        if not vals:
            return default
        return next(iter(vals)) if len(vals) == 1 else "mixed"

    config = {
        "mode": _agree("mode", "blind"),
        "max_turns": _agree("max_turns", max_turns),
        "timeout_s": _agree("timeout_s"),
        "stop_on_solve": _agree("stop_on_solve"),
        "preserve_pocs": _agree("preserve_pocs"),
        # No default: a sweep whose cells disagree, or that recorded nothing,
        # should say so rather than inherit a claim about how it was graded.
        "grading": _agree("grading"),
        # The image tag is what CHOSE that grading, so the page can say which
        # artifact produced these numbers rather than only how they were judged.
        # A sweep whose bugs pin their own images reads "mixed", which is true.
        "image_tag": _agree("image_tag"),
    }

    # Per (challenge, model): the union across that pair's samples, clustered.
    # Keyed with a separator no alias or model id contains, so the page can look
    # a pair up without shipping a nested map.
    pairs = {f"{b} {m}": {"crashes": len(sm), "bugs": bug_count(sm)}
             for (b, m), sm in sorted(pair_sigs.items())}
    # Sweep headline: per challenge, the union across every model, clustered.
    # Two models that both found one defect found ONE bug on that challenge —
    # summing the per-model answers would report two.
    totals = {
        "crashes": sum(len(sm) for sm in challenge_sigs.values()),
        "bugs": sum(bug_count(sm) for sm in challenge_sigs.values()),
        "challenges_with_crashes": sum(1 for sm in challenge_sigs.values() if sm),
    }

    return {
        "exp": exp or exp_dir.name,
        # The two measurements this page reports side by side, never one instead
        # of the other: `crashes` = distinct ways a fault was reached (a real
        # skill), `bugs` = distinct defects those faults point at (what a
        # bug-finding benchmark is asking).
        "pairs": pairs,
        "totals": totals,
        # Does ANY cell here carry an oracle ladder verdict? A sweep run entirely
        # against self-contained images does not, and the page uses this to show
        # what was measured (distinct crashes) instead of a grid of zeroes that
        # reads as five failed checks per cell.
        "graded_ladder": any(c.get("has_ladder") for c in cells),
        "models": models,
        "bugs": bugs,
        "samples": samples,
        "max_turns": max_turns,
        "config": config,
        "total_cost": total_cost if total_cost is not None else cost_sum,
        "elapsed_s": elapsed_s,
        "max_score": max_score,
        # The legacy difficulty score (out of max_score) is kept alongside this
        # one, not replaced: it is the number every earlier result is expressed
        # in, and dropping it would make this run incomparable with all of them.
        "crash_score": ({
            "batch_uid": crash_score.get("batch_uid"),
            "cap_per_challenge": crash_score.get("cap_per_challenge"),
            "models": crash_score.get("models", []),
        } if crash_score else None),
        "cells": cells,
    }


def write_summary(exp_dir: str | Path, **meta) -> Path:
    """Build the summary and write <exp_dir>/index.html (self-contained)."""
    exp_dir = Path(exp_dir)
    data = build_summary(exp_dir, **meta)
    tmpl = _TEMPLATE.read_text()
    # Inject as the textContent of <script type="application/json">; escape the
    # only sequence that could close that tag early. The blob is answer-free.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (tmpl.replace("__SUMMARY_JSON__", blob)
                .replace("__EXP__", data["exp"]))
    out = exp_dir / "index.html"
    out.write_text(html)
    return out
