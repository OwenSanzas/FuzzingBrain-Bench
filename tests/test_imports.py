"""Every package module imports cleanly (without provider SDKs loaded)."""
import importlib

import pytest

# SDK-free modules: the concrete provider backends lazy-import their SDKs only
# inside make_backend, so these all import under bare python.
MODULES = [
    "fbbench", "fbbench.paths", "fbbench.env", "fbbench.prompts",
    "fbbench.models", "fbbench.models.catalog", "fbbench.models.pricing",
    "fbbench.grading", "fbbench.grading.bench_yaml", "fbbench.grading.grader",
    "fbbench.cli.console", "fbbench.cli.commands", "fbbench.cli.main",
    "fbbench.runner", "fbbench.runner.episode", "fbbench.runner.backends",
    "fbbench.runner.mcp_client",
    "fbbench.sweep.orchestrator", "fbbench.sweep.regression", "fbbench.sweep.codex",
]


@pytest.mark.parametrize("mod", MODULES)
def test_import(mod):
    importlib.import_module(mod)
