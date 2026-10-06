"""Minimal .env loader (no extra dependency). Existing environment variables win."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and k not in os.environ:
            os.environ[k] = v


def require(*names: str) -> dict[str, str]:
    load_dotenv()
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)} (set them in .env, see .env.example)")
    return {n: os.environ[n] for n in names}
