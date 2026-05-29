"""End-to-end oracle check: a reference PoC fires its full K_b unanimously.

Skipped when bin/mcp-server is not built (CI builds it first via `make`).
"""
import pytest

from fbbench.grading import capability_set, find_bug, grade_blob
from fbbench.paths import SERVER

pytestmark = pytest.mark.skipif(not SERVER.exists(), reason="bin/mcp-server not built")


def test_reference_poc_passes():
    bd = find_bug("netsnmp-vacm-parse-npd")
    r, _ = grade_blob(bd, bd / "poc" / "poc.bin", rounds=3)
    caps = r["capabilities"]
    assert r.get("agreed") is True
    for flag in capability_set(bd):
        assert caps.get(flag) == "fired", f"{flag} did not fire"
