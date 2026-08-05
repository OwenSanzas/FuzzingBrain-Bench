"""Which challenge image a run reaches for — and what that implies about grading.

The tag is the whole difference between the two ways a run can be graded, so it
is not just a registry detail:

  * ``local-v1`` — the self-contained image. It carries its own harness builds
    and grades the agent's candidates INSIDE the container, with no network. It
    has no answer key, so it computes no capability ladder and no ``solved``; it
    counts DISTINCT CRASHES (crash type + top frames). This is the default: a
    clone with Docker and an API key can run the whole benchmark offline.

  * ``latest`` — the remote-oracle image. Candidates are POSTed to the grading
    service, which owns the answer keys and returns the five-rung ladder plus
    an authoritative ``solved``. Needs the service to be reachable.

Everything downstream (the leaderboard columns, the dashboard, index.html) keys
off :func:`grades_locally`, so the two modes never have to be spelled out twice.
"""
from __future__ import annotations

DEFAULT_IMAGE_PREFIX = "docker.io/osanzas/fbbench-challenge-"

#: The tag used when nobody says otherwise — offline, self-contained grading.
DEFAULT_IMAGE_TAG = "local-v1"

#: The one tag that means "grade against the remote oracle".
REMOTE_TAG = "latest"


def grades_locally(image_tag: str | None) -> bool:
    """True when this tag grades in-image (no oracle, no ladder, no answer key).

    ``None`` means the caller never set one, which is the default — and the
    default is local.
    """
    return (image_tag or DEFAULT_IMAGE_TAG) != REMOTE_TAG
