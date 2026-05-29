"""bench.yaml reading + bug discovery."""
from fbbench.grading import capability_set, find_bug, list_bugs, read_bench


def test_list_bugs_nonempty():
    bugs = list_bugs()
    assert len(bugs) >= 30
    assert "netsnmp-vacm-parse-npd" in [b for b, _ in bugs]


def test_capability_set_is_a_ladder_subset():
    bd = find_bug("netsnmp-vacm-parse-npd")
    assert bd is not None
    kb = capability_set(bd)
    assert kb and set(kb) <= {"reach", "crash", "class", "site"}


def test_read_bench_scalars():
    bd = find_bug("netsnmp-vacm-parse-npd")
    bench = read_bench(bd / "bench.yaml")
    assert bench.get("bug_id") == "netsnmp-vacm-parse-npd"


def test_find_bug_missing_returns_none():
    assert find_bug("does-not-exist") is None
