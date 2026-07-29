from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .base import CachedBlob, ConsumptionSignal, RatingSummary, Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS upstream_cache (
    key         TEXT PRIMARY KEY,
    body        TEXT NOT NULL,
    digest      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    changed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rating (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id   INTEGER NOT NULL,
    score       INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    user_id     TEXT    NOT NULL,
    location_id INTEGER,
    served_on   TEXT,
    comment     TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rating_recipe ON rating(recipe_id);

CREATE TABLE IF NOT EXISTS waste_observation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id   INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    served_on   TEXT    NOT NULL,
    meal        INTEGER NOT NULL,
    prepared_lb REAL    NOT NULL,
    wasted_lb   REAL    NOT NULL,
    source      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_waste_recipe ON waste_observation(recipe_id);

CREATE TABLE IF NOT EXISTS attendance_intent (
    user_id     TEXT    NOT NULL,
    location_id INTEGER NOT NULL,
    served_on   TEXT    NOT NULL,
    meal        INTEGER NOT NULL,
    attending   INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, location_id, served_on, meal)
);
CREATE INDEX IF NOT EXISTS idx_intent_service
    ON attendance_intent(location_id, served_on, meal);
"""


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


class SqliteStore(Store):

    def __init__(self, path: Path):
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        self._local.conn = conn
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        return conn if conn is not None else self._connect()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


    def get_cached(self, key: str) -> CachedBlob | None:
        row = self._conn.execute(
            "SELECT key, body, digest, fetched_at, changed_at "
            "FROM upstream_cache WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return CachedBlob(
            key=row["key"],
            body=row["body"],
            digest=row["digest"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            changed_at=datetime.fromisoformat(row["changed_at"]),
        )

    def put_cached(self, key: str, body: str, digest: str, now: datetime) -> None:
        stamp = now.isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO upstream_cache (key, body, digest, fetched_at, changed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    body       = excluded.body,
                    digest     = excluded.digest,
                    fetched_at = excluded.fetched_at,
                    changed_at = CASE
                        WHEN upstream_cache.digest = excluded.digest
                        THEN upstream_cache.changed_at
                        ELSE excluded.changed_at
                    END
                """,
                (key, body, digest, stamp, stamp),
            )
            self._conn.commit()

    def touch_cached(self, key: str, now: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE upstream_cache SET fetched_at = ? WHERE key = ?",
                (now.isoformat(), key),
            )
            self._conn.commit()


    def add_rating(
        self,
        recipe_id: int,
        score: int,
        user_id: str,
        location_id: int | None,
        served_on: str | None,
        comment: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO rating "
                "(recipe_id, score, user_id, location_id, served_on, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    recipe_id,
                    score,
                    user_id,
                    location_id,
                    served_on,
                    comment,
                    datetime.utcnow().isoformat(),
                ),
            )
            self._conn.commit()

    def rating_summary(self, recipe_ids: list[int]) -> dict[int, RatingSummary]:
        if not recipe_ids:
            return {}
        rows = self._conn.execute(
            f"SELECT recipe_id, COUNT(*) AS n, AVG(score) AS avg_score "
            f"FROM rating WHERE recipe_id IN ({_placeholders(len(recipe_ids))}) "
            f"GROUP BY recipe_id",
            recipe_ids,
        ).fetchall()
        return {
            r["recipe_id"]: RatingSummary(
                recipe_id=r["recipe_id"],
                count=r["n"],
                average=round(r["avg_score"], 2),
            )
            for r in rows
        }


    def add_waste_observation(
        self,
        recipe_id: int,
        location_id: int,
        served_on: str,
        meal: int,
        prepared_lb: float,
        wasted_lb: float,
        source: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO waste_observation "
                "(recipe_id, location_id, served_on, meal, prepared_lb, wasted_lb, "
                " source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    recipe_id,
                    location_id,
                    served_on,
                    meal,
                    prepared_lb,
                    wasted_lb,
                    source,
                    datetime.utcnow().isoformat(),
                ),
            )
            self._conn.commit()

    def consumption_signals(self, recipe_ids: list[int]) -> dict[int, ConsumptionSignal]:
        if not recipe_ids:
            return {}
        rows = self._conn.execute(
            f"""
            SELECT recipe_id,
                   COUNT(*)          AS n,
                   SUM(prepared_lb)  AS prepared,
                   SUM(wasted_lb)    AS wasted
            FROM waste_observation
            WHERE recipe_id IN ({_placeholders(len(recipe_ids))})
            GROUP BY recipe_id
            """,
            recipe_ids,
        ).fetchall()
        out: dict[int, ConsumptionSignal] = {}
        for r in rows:
            prepared = r["prepared"] or 0.0
            if prepared <= 0:
                continue
            rate = 1.0 - (r["wasted"] or 0.0) / prepared
            out[r["recipe_id"]] = ConsumptionSignal(
                recipe_id=r["recipe_id"],
                rate=round(max(0.0, min(1.0, rate)), 4),
                observations=r["n"],
            )
        return out


    def set_attendance_intent(
        self, user_id: str, location_id: int, served_on: str, meal: int, attending: bool
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO attendance_intent
                    (user_id, location_id, served_on, meal, attending, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, location_id, served_on, meal) DO UPDATE SET
                    attending  = excluded.attending,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    location_id,
                    served_on,
                    meal,
                    1 if attending else 0,
                    datetime.utcnow().isoformat(),
                ),
            )
            self._conn.commit()

    def attendance_counts(
        self, location_id: int, served_on: str, meal: int
    ) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT "
            "  SUM(CASE WHEN attending = 1 THEN 1 ELSE 0 END) AS yes, "
            "  SUM(CASE WHEN attending = 0 THEN 1 ELSE 0 END) AS no "
            "FROM attendance_intent "
            "WHERE location_id = ? AND served_on = ? AND meal = ?",
            (location_id, served_on, meal),
        ).fetchone()
        return int(row["yes"] or 0), int(row["no"] or 0)
