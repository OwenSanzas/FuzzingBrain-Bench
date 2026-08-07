"""Which challenge image a run reaches for.

There is one image per challenge:

    docker.io/osanzas/fbbench-challenge-<alias>:latest

It carries its own sanitizer harness and grades every candidate inside the
container, with no network. It has no answer key, so it computes no capability
ladder and no ``solved`` — it counts the distinct crash signatures the agent
produced, and that count is the score.

Nothing infers the grading mode from the tag. The image decides (mcp-server
grades in-image when a harness is baked in) and the episode records which grader
answered, as ``config.grading``.
"""
from __future__ import annotations

DEFAULT_IMAGE_PREFIX = "docker.io/osanzas/fbbench-challenge-"
DEFAULT_IMAGE_TAG = "latest"
