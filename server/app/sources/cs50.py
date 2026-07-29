from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import CACHE_TTL_HOURS, CS50_BASE, UPSTREAM_CONCURRENCY, UPSTREAM_TIMEOUT_S
from ..store.base import Store

log = logging.getLogger("veritaste.cs50")

MEAL_NAMES = {0: "Breakfast", 1: "Lunch", 2: "Dinner"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiningSource:

    def __init__(self, store: Store, ttl_hours: int = CACHE_TTL_HOURS):
        self._store = store
        self._ttl = timedelta(hours=ttl_hours)
        self._client = httpx.Client(
            base_url=CS50_BASE,
            timeout=UPSTREAM_TIMEOUT_S,
            headers={"User-Agent": "Veritaste/0.1 (ENSC S-106 class project)"},
        )
        self._pool = ThreadPoolExecutor(
            max_workers=UPSTREAM_CONCURRENCY, thread_name_prefix="cs50"
        )
        self._keylocks: dict[str, threading.Lock] = {}
        self._keylocks_guard = threading.Lock()

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        self._client.close()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._keylocks_guard:
            return self._keylocks.setdefault(key, threading.Lock())


    def get(
        self, key: str, path: str, params: dict[str, Any] | None = None, force: bool = False
    ) -> tuple[Any, str]:
        cached = self._store.get_cached(key)
        now = _utcnow()

        if cached and not force and self._is_fresh(cached.fetched_at, now):
            return json.loads(cached.body), "fresh"

        with self._lock_for(key):
            cached = self._store.get_cached(key)
            now = _utcnow()
            if cached and not force and self._is_fresh(cached.fetched_at, now):
                return json.loads(cached.body), "fresh"
            return self._refresh(key, path, params, cached, now)

    def _is_fresh(self, fetched_at: datetime, now: datetime) -> bool:
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return (now - fetched_at) < self._ttl

    def _refresh(self, key, path, params, cached, now) -> tuple[Any, str]:
        try:
            resp = self._client.get(path, params=params)
            resp.raise_for_status()
            raw = resp.text
        except Exception as exc:
            if cached is not None:
                log.warning("upstream failed for %s (%s); serving stale copy", key, exc)
                return json.loads(cached.body), "stale-upstream-error"
            log.error("upstream failed for %s with no cached copy: %s", key, exc)
            raise

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if cached is not None and cached.digest == digest:
            self._store.touch_cached(key, now)
            return json.loads(raw), "unchanged"

        self._store.put_cached(key, raw, digest, now)
        return json.loads(raw), "refreshed"


    def locations(self, force: bool = False) -> list[dict]:
        data, _ = self.get("locations", "/locations", force=force)
        return data

    def categories(self, force: bool = False) -> list[dict]:
        data, _ = self.get("categories", "/categories", force=force)
        return data

    def day_rows(self, date: str, location: int) -> tuple[list[dict], str]:
        key = f"menus:{date}:{location}"
        return self.get(key, "/menus", {"date": date, "location": location})

    def menu_rows(self, date: str, location: int, meal: int) -> tuple[list[dict], str]:
        rows, status = self.day_rows(date, location)
        return [r for r in rows if r.get("meal") == meal], status

    def meals_served(self, date: str, location: int) -> list[int]:
        rows, _ = self.day_rows(date, location)
        return sorted({r["meal"] for r in rows if "meal" in r})

    def service_profile(self, date: str, location: int) -> list[int]:
        anchor = _dt.date.fromisoformat(date)
        iso = anchor.isocalendar()
        key = f"profile:{location}:{iso[0]}-W{iso[1]:02d}"

        cached = self._store.get_cached(key)
        now = _utcnow()
        if cached and self._is_fresh(cached.fetched_at, now):
            return json.loads(cached.body)

        monday = anchor - _dt.timedelta(days=anchor.weekday())
        seen: set[int] = set()
        for offset in range(7):
            day = (monday + _dt.timedelta(days=offset)).isoformat()
            try:
                rows, _status = self.day_rows(day, location)
            except Exception as exc:
                log.warning("profile: %s %s failed: %s", location, day, exc)
                continue
            seen.update(r["meal"] for r in rows if "meal" in r)

        body = json.dumps(sorted(seen))
        self._store.put_cached(
            key, body, hashlib.sha256(body.encode()).hexdigest(), now
        )
        return sorted(seen)

    def cache_age_hours(self, date: str, location: int) -> float | None:
        entry = self._store.get_cached(f"menus:{date}:{location}")
        if entry is None:
            return None
        fetched = entry.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return round((_utcnow() - fetched).total_seconds() / 3600.0, 1)

    def recipe(self, recipe_id: int) -> dict:
        data, _ = self.get(f"recipe:{recipe_id}", f"/recipes/{recipe_id}")
        return data

    def recipes(self, recipe_ids: list[int]) -> dict[int, dict]:
        if not recipe_ids:
            return {}
        out: dict[int, dict] = {}
        futures = {rid: self._pool.submit(self.recipe, rid) for rid in recipe_ids}
        for rid, fut in futures.items():
            try:
                out[rid] = fut.result()
            except Exception as exc:
                log.warning("recipe %s failed: %s", rid, exc)
        return out
