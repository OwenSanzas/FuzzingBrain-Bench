"""Which challenge image a run reaches for.

There is one image per challenge:

    docker.io/osanzas/fbbench-challenge-<alias>:latest

It carries its own sanitizer harness and grades every candidate inside the
container, with no network. It counts the distinct crash signatures the agent
produced, and that count is the score.

Nothing infers anything from the tag. There is one grader — the image's own —
and the episode records that it answered, as ``config.grading``.
"""
from __future__ import annotations

DEFAULT_IMAGE_PREFIX = "docker.io/osanzas/fbbench-challenge-"
# One published tag. It was configurable while the tag chose a grader -- :latest
# graded remotely, :local-v1 in-image -- and there is nothing left for it to
# select, so it is a constant rather than a flag nobody can usefully set.
DEFAULT_IMAGE_TAG = "latest"


def challenge_image(alias: str, prefix: str = DEFAULT_IMAGE_PREFIX) -> str:
    """The published image for one challenge."""
    return f"{prefix}{alias}:{DEFAULT_IMAGE_TAG}"
