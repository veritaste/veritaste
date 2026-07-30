from __future__ import annotations

from ..config import DB_PATH, STORE_BACKEND
from .base import (CachedBlob, ConsumptionSignal, PushSub, RatingSummary,
                   RewardGrant, RewardSummary, Store)
from .sqlite_store import SqliteStore

__all__ = [
    "Store",
    "CachedBlob",
    "RatingSummary",
    "ConsumptionSignal",
    "RewardGrant",
    "RewardSummary",
    "PushSub",
    "build_store",
]


def build_store() -> Store:
    backend = STORE_BACKEND.lower()
    if backend == "sqlite":
        return SqliteStore(DB_PATH)
    raise ValueError(
        f"Unknown VERITASTE_STORE={STORE_BACKEND!r}. Supported backends: sqlite"
    )
