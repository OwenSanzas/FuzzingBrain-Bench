"""Deterministic grade() oracle + bench.yaml reading, usable without an LLM."""
from fbbench.grading.bench_yaml import (
    DEFAULT_KB, capability_set, find_bug, list_bugs, read_bench,
)
from fbbench.grading.grader import FLAGS, grade_blob

__all__ = [
    "read_bench", "capability_set", "find_bug", "list_bugs", "DEFAULT_KB",
    "grade_blob", "FLAGS",
]
