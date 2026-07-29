from __future__ import annotations

import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SERVER_DIR.parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


DB_PATH = Path(os.environ.get("VERITASTE_DB", SERVER_DIR / "veritaste.db"))

STORE_BACKEND = os.environ.get("VERITASTE_STORE", "sqlite")

CS50_BASE = os.environ.get("CS50_BASE", "https://api.cs50.io/dining")

CACHE_TTL_HOURS = _env_int("VERITASTE_CACHE_TTL_HOURS", 12)

UPSTREAM_TIMEOUT_S = _env_int("VERITASTE_UPSTREAM_TIMEOUT", 20)
UPSTREAM_CONCURRENCY = _env_int("VERITASTE_UPSTREAM_CONCURRENCY", 6)

WEB_DIR = Path(os.environ.get("VERITASTE_WEB", PROJECT_DIR / "web"))

ECS_APIKEY = os.environ.get("ECS_APIKEY", "")
