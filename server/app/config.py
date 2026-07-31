from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE = os.environ.get("VERITASTE_TIMEZONE", "America/New_York")
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError as exc:
    raise ValueError(
        f"VERITASTE_TIMEZONE={TIMEZONE!r} could not be resolved. Check the name "
        "against the IANA database, and note that Windows needs the `tzdata` package."
    ) from exc

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

MODE = os.environ.get("VERITASTE_MODE", "production").strip().lower()
if MODE not in ("production", "demo"):
    raise ValueError(
        f'VERITASTE_MODE={MODE!r} is not recognised. Use "production" or "demo".'
    )

DEMO_MODE = MODE == "demo"

RATING_RECENT_DAYS = _env_int("VERITASTE_RATING_RECENT_DAYS", 30)

VAPID_PUBLIC = os.environ.get("VERITASTE_VAPID_PUBLIC", "")
VAPID_PRIVATE = os.environ.get("VERITASTE_VAPID_PRIVATE", "")
VAPID_SUB = os.environ.get("VERITASTE_VAPID_SUB", "https://veritaste.org")

STAFF_PASSCODE = os.environ.get("VERITASTE_STAFF_PASSCODE", "")

UNLOCK_WINDOW_S = _env_int("VERITASTE_UNLOCK_WINDOW_S", 15)

GRILL_WAIT_CAP_MIN = _env_int("VERITASTE_GRILL_WAIT_CAP_MIN", 20)
GRILL_APP_CAP_DEFAULT = _env_int("VERITASTE_GRILL_APP_CAP", 4)
GRILL_COOK_S_DEFAULT = _env_int("VERITASTE_GRILL_COOK_S", 240)
GRILL_HEARTBEAT_STALE_S = _env_int("VERITASTE_GRILL_HEARTBEAT_S", 20)
GRILL_LAST_CALL_MIN = _env_int("VERITASTE_GRILL_LAST_CALL_MIN", 15)

ECS_APIKEY = os.environ.get("ECS_APIKEY", "")
