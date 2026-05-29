"""FuzzingBrain Bench — a capability-ladder benchmark for LLM-driven
vulnerability reproduction on real C / C++ / Java open-source bugs.

Public surface:
  fbbench.cli      — the `fb-bench` command-line interface
  fbbench.runner   — the episode driver (one LLM agent vs one bug)
  fbbench.grading  — the deterministic grade() oracle, usable standalone
  fbbench.models   — model catalog, provider routing, pricing
  fbbench.sweep    — batch orchestration across (model x bug x sample)
"""
__version__ = "1.0.0"
