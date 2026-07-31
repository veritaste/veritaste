from __future__ import annotations

import csv
import hashlib
import io
import secrets

KEY_PREFIX = "vrk_"

SMALL_SAMPLE_MIN = 3

TREND_DELTA = 0.25

NAME_RESOLVE_MAX = 150


def mint_key() -> tuple[str, str]:
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    return token, hash_key(token)


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_token(header: str | None) -> str | None:
    if not header or not header.startswith("Bearer "):
        return None
    return header[len("Bearer "):].strip() or None


def has_scope(scopes: str, needed: str) -> bool:
    return needed in scopes.split()


def trend(recent_average: float | None, prior_average: float | None) -> str | None:
    if recent_average is None or prior_average is None:
        return None
    delta = recent_average - prior_average
    if delta >= TREND_DELTA:
        return "rising"
    if delta <= -TREND_DELTA:
        return "falling"
    return "steady"


def to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
