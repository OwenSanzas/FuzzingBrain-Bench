"""`.env` loading + provider-key detection (stdlib only)."""
from __future__ import annotations

import os
from pathlib import Path

from fbbench.models import PROVIDER_KEY_ENV
from fbbench.paths import REPO


def read_dotenv(repo_root: Path | None = None) -> dict[str, str]:
    """Parse ./.env into a dict. Best-effort; ignores comments / blanks."""
    env_path = (repo_root or REPO) / ".env"
    out: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


def load_dotenv(repo_root: Path | None = None) -> None:
    """Populate os.environ from .env without overriding existing vars."""
    for k, v in read_dotenv(repo_root).items():
        os.environ.setdefault(k, v)


def detect_provider() -> tuple[str | None, list[str]]:
    """Return (preferred_provider, providers_with_a_key_loaded)."""
    env = {**read_dotenv(), **os.environ}
    have = [p for p, key in PROVIDER_KEY_ENV.items() if env.get(key)]
    for p in ("anthropic", "openai", "gemini"):
        if p in have:
            return p, have
    return None, have
