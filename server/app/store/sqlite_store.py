from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .base import (CachedBlob, ConsumptionSignal, PushSub, RatingSummary,
                   RewardGrant, RewardSummary, Store)

SCHEMA = """
CREATE TABLE IF NOT EXISTS upstream_cache (
    key         TEXT PRIMARY KEY,
    body        TEXT NOT NULL,
    digest      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    changed_at  TEXT NOT NULL
);

-- One vote per student per dish per hall. The primary key is the whole point:
-- ratings were previously an append-only INSERT with no key, so a single account
-- had cast 205 votes on one dish and every one counted toward the average.
--
-- The hall belongs in the key because the same recipe id cooked at two Houses is
-- two different executions — a kitchen wants to know how theirs scores.
CREATE TABLE IF NOT EXISTS rating (
    user_id     TEXT    NOT NULL,
    recipe_id   INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    score       INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    served_on   TEXT,
    comment     TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, recipe_id, location_id)
);
CREATE INDEX IF NOT EXISTS idx_rating_dish ON rating(recipe_id, location_id);
CREATE INDEX IF NOT EXISTS idx_rating_updated ON rating(updated_at);

-- Immutable record of every vote and every change. The upsert above keeps only
-- a student's current opinion; this keeps the fact that it moved, and when, so
-- a hall can watch a dish's standing rise or fall instead of reading a lifetime
-- average that a bad month can never shift.
CREATE TABLE IF NOT EXISTS rating_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    recipe_id   INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    score       INTEGER NOT NULL,
    served_on   TEXT,
    comment     TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rating_hist
    ON rating_history(recipe_id, location_id, created_at);

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

-- Simulated BoardPlus ledger. The composite primary key IS the anti-farming
-- mechanism: one credit per student per location per service per kind, enforced
-- by the database rather than by a caller who might forget. `meal` carries
-- rewards.DAY_SCOPED (-1) for credits that belong to a whole day.
--
-- Two dates on purpose. `served_on` is the service the credit was earned for and
-- is part of the key; it comes from the client and may be any date. `granted_on`
-- is the local Boston date the credit was recorded, and it is what the daily
-- ceiling counts — the one value a student cannot choose.
CREATE TABLE IF NOT EXISTS reward_grant (
    user_id     TEXT    NOT NULL,
    location_id INTEGER NOT NULL,
    served_on   TEXT    NOT NULL,
    meal        INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    cents       INTEGER NOT NULL,
    granted_on  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, location_id, served_on, meal, kind)
);
CREATE INDEX IF NOT EXISTS idx_reward_day ON reward_grant(user_id, granted_on);

-- Web Push subscriptions, keyed on the endpoint the browser issued. Role and
-- House are copied from the session because there is no user table to join.
CREATE TABLE IF NOT EXISTS push_subscription (
    endpoint    TEXT PRIMARY KEY,
    user_sub    TEXT NOT NULL,
    affiliation TEXT NOT NULL,
    house_key   TEXT,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscription(user_sub);

-- Scoped, revocable credentials for pulling aggregate reports. Not sessions:
-- these are for machines — a BI tool, a spreadsheet refresh, an AI assistant's
-- tool wrapper — so they are bearer tokens stored only as a hash, provisioned
-- per integration and revocable per integration. Revocation is an UPDATE, not
-- a DELETE: the row is the audit record of the key having existed.
CREATE TABLE IF NOT EXISTS report_key (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT    NOT NULL,
    key_hash     TEXT    NOT NULL UNIQUE,
    scopes       TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    revoked_at   TEXT,
    last_used_at TEXT
);

-- The Availability Board's current state: at most one mark per dish per hall.
-- Marks are scoped to the Boston service day (`marked_on`) — a dish 86'd at
-- dinner is not still 86'd at tomorrow's breakfast; reads filter on the day
-- and stale rows are simply overwritten by the next mark.
-- `note` is the routing line students see ("Salad bar is stocked until 8:00").
CREATE TABLE IF NOT EXISTS stock_item (
    location_id INTEGER NOT NULL,
    recipe_id   INTEGER NOT NULL,
    status      TEXT    NOT NULL CHECK (status IN ('low','out')),
    note        TEXT,
    marked_on   TEXT    NOT NULL,
    marked_at   TEXT    NOT NULL,
    PRIMARY KEY (location_id, recipe_id)
);

-- Append-only history of every mark and restock. The upsert above keeps only
-- the present; this keeps what happened — which dishes ran out, when, and how
-- often — which is exactly the record production planning wants back.
CREATE TABLE IF NOT EXISTS stock_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    recipe_id   INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    note        TEXT,
    user_id     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_event
    ON stock_event(location_id, created_at);
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
        location_id: int,
        served_on: str | None,
        comment: str | None,
        recent_days: int,
    ) -> bool:
        now = datetime.utcnow().isoformat()
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM rating WHERE user_id = ? AND recipe_id = ? "
                "AND location_id = ?",
                (user_id, recipe_id, location_id),
            ).fetchone()
            self._conn.execute(
                """
                INSERT INTO rating
                    (user_id, recipe_id, location_id, score, served_on, comment,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, recipe_id, location_id) DO UPDATE SET
                    score      = excluded.score,
                    served_on  = excluded.served_on,
                    comment    = excluded.comment,
                    updated_at = excluded.updated_at
                """,
                (user_id, recipe_id, location_id, score, served_on, comment, now, now),
            )
            self._conn.execute(
                "INSERT INTO rating_history "
                "(user_id, recipe_id, location_id, score, served_on, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, recipe_id, location_id, score, served_on, comment, now),
            )
            self._conn.commit()
        return existing is not None

    def rating_summary(
        self, recipe_ids: list[int], location_id: int | None, recent_days: int
    ) -> dict[int, RatingSummary]:
        if not recipe_ids:
            return {}
        cutoff = (datetime.utcnow() - timedelta(days=recent_days)).isoformat()
        where_loc = " AND location_id = ?" if location_id is not None else ""
        rows = self._conn.execute(
            f"""
            SELECT recipe_id,
                   COUNT(*)                                   AS n,
                   AVG(score)                                 AS avg_score,
                   SUM(CASE WHEN updated_at >= ? THEN 1 ELSE 0 END)      AS n_recent,
                   AVG(CASE WHEN updated_at >= ? THEN score END)         AS avg_recent
            FROM rating
            WHERE recipe_id IN ({_placeholders(len(recipe_ids))}){where_loc}
            GROUP BY recipe_id
            """,
            [cutoff, cutoff] + list(recipe_ids) + ([location_id] if location_id is not None else []),
        ).fetchall()
        return {
            r["recipe_id"]: RatingSummary(
                recipe_id=r["recipe_id"],
                count=r["n"],
                average=round(r["avg_score"], 2),
                recent_count=int(r["n_recent"] or 0),
                recent_average=(round(r["avg_recent"], 2)
                                if r["avg_recent"] is not None else None),
            )
            for r in rows
        }

    def user_rating(
        self, user_id: str, recipe_id: int, location_id: int
    ) -> int | None:
        row = self._conn.execute(
            "SELECT score FROM rating WHERE user_id = ? AND recipe_id = ? "
            "AND location_id = ?",
            (user_id, recipe_id, location_id),
        ).fetchone()
        return None if row is None else int(row["score"])

    def rating_trend(
        self, recipe_id: int, location_id: int | None, buckets: int, days: int
    ) -> list[tuple[str, int, float]]:
        cutoff = (datetime.utcnow() - timedelta(days=buckets * days)).isoformat()
        params: list = [recipe_id, cutoff]
        where_loc = ""
        if location_id is not None:
            where_loc = " AND location_id = ?"
            params.append(location_id)
        rows = self._conn.execute(
            f"SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n, "
            f"AVG(score) AS avg_score FROM rating_history "
            f"WHERE recipe_id = ? AND created_at >= ?{where_loc} "
            f"GROUP BY day ORDER BY day",
            params,
        ).fetchall()
        return [(r["day"], int(r["n"]), round(r["avg_score"], 2)) for r in rows]


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


    def grant_reward(
        self,
        user_id: str,
        location_id: int,
        served_on: str,
        meal: int,
        kind: str,
        cents: int,
        granted_on: str,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO reward_grant
                    (user_id, location_id, served_on, meal, kind, cents,
                     granted_on, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, location_id, served_on, meal, kind) DO NOTHING
                """,
                (user_id, location_id, served_on, meal, kind, cents,
                 granted_on, datetime.utcnow().isoformat()),
            )
            self._conn.commit()
        return cents if cur.rowcount else 0

    def reward_summary(self, user_id: str, on_date: str) -> RewardSummary:
        totals = self._conn.execute(
            "SELECT COALESCE(SUM(cents), 0) AS total, COUNT(*) AS n "
            "FROM reward_grant WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        day_row = self._conn.execute(
            "SELECT COALESCE(SUM(cents), 0) AS total FROM reward_grant "
            "WHERE user_id = ? AND granted_on = ?",
            (user_id, on_date),
        ).fetchone()
        rows = self._conn.execute(
            "SELECT kind, cents, location_id, served_on, meal, granted_on, created_at "
            "FROM reward_grant WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT 8",
            (user_id,),
        ).fetchall()
        return RewardSummary(
            pending_cents=int(totals["total"]),
            grant_count=int(totals["n"]),
            day_cents=int(day_row["total"]),
            recent=tuple(
                RewardGrant(
                    kind=r["kind"], cents=r["cents"], location_id=r["location_id"],
                    served_on=r["served_on"], meal=r["meal"],
                    granted_on=r["granted_on"], created_at=r["created_at"],
                )
                for r in rows
            ),
        )


    def put_push_sub(self, sub: PushSub) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO push_subscription
                    (endpoint, user_sub, affiliation, house_key, p256dh, auth, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    user_sub    = excluded.user_sub,
                    affiliation = excluded.affiliation,
                    house_key   = excluded.house_key,
                    p256dh      = excluded.p256dh,
                    auth        = excluded.auth
                """,
                (sub.endpoint, sub.user_sub, sub.affiliation, sub.house_key,
                 sub.p256dh, sub.auth, datetime.utcnow().isoformat()),
            )
            self._conn.commit()

    def delete_push_sub(self, endpoint: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM push_subscription WHERE endpoint = ?", (endpoint,)
            )
            self._conn.commit()

    def push_subs(
        self, user_id: str | None = None, affiliation: str | None = None
    ) -> list[PushSub]:
        sql = ("SELECT endpoint, user_sub, affiliation, house_key, p256dh, auth "
               "FROM push_subscription")
        clauses, params = [], []
        if user_id is not None:
            clauses.append("user_sub = ?")
            params.append(user_id)
        if affiliation is not None:
            clauses.append("affiliation = ?")
            params.append(affiliation)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [
            PushSub(
                endpoint=r["endpoint"], user_sub=r["user_sub"],
                affiliation=r["affiliation"], house_key=r["house_key"],
                p256dh=r["p256dh"], auth=r["auth"],
            )
            for r in self._conn.execute(sql, params).fetchall()
        ]


    def create_report_key(self, label: str, scopes: str, key_hash: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO report_key (label, key_hash, scopes, created_at) "
                "VALUES (?, ?, ?, ?)",
                (label, key_hash, scopes, datetime.utcnow().isoformat()),
            )
            self._conn.commit()
            return cur.lastrowid

    def report_keys(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, label, scopes, created_at, revoked_at, last_used_at "
            "FROM report_key ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def revoke_report_key(self, key_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE report_key SET revoked_at = ? "
                "WHERE id = ? AND revoked_at IS NULL",
                (datetime.utcnow().isoformat(), key_id),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def verify_report_key(self, key_hash: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, label, scopes FROM report_key "
            "WHERE key_hash = ? AND revoked_at IS NULL",
            (key_hash,),
        ).fetchone()
        if row is None:
            return None
        with self._lock:
            self._conn.execute(
                "UPDATE report_key SET last_used_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), row["id"]),
            )
            self._conn.commit()
        return dict(row)

    def ratings_report(self, location_id: int | None, days: int) -> list[dict]:
        cut = (datetime.utcnow() - timedelta(days=days)).isoformat()
        prior_cut = (datetime.utcnow() - timedelta(days=2 * days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT recipe_id, location_id,
                   COUNT(*)                                          AS votes,
                   ROUND(AVG(score), 2)                              AS average,
                   SUM(CASE WHEN updated_at >= ? THEN 1 ELSE 0 END)  AS recent_votes,
                   ROUND(AVG(CASE WHEN updated_at >= ? THEN score END), 2)
                                                                     AS recent_average,
                   MAX(updated_at)                                   AS last_rated
            FROM rating
            WHERE (? IS NULL OR location_id = ?)
            GROUP BY recipe_id, location_id
            ORDER BY recent_votes DESC, votes DESC, recipe_id
            """,
            (cut, cut, location_id, location_id),
        ).fetchall()
        prior = {
            (r["recipe_id"], r["location_id"]): r["prior_average"]
            for r in self._conn.execute(
                """
                SELECT recipe_id, location_id,
                       ROUND(AVG(score), 2) AS prior_average
                FROM rating_history
                WHERE created_at >= ? AND created_at < ?
                  AND (? IS NULL OR location_id = ?)
                GROUP BY recipe_id, location_id
                """,
                (prior_cut, cut, location_id, location_id),
            ).fetchall()
        }
        out = []
        for r in rows:
            d = dict(r)
            d["prior_average"] = prior.get((r["recipe_id"], r["location_id"]))
            out.append(d)
        return out

    def attendance_report(
        self, location_id: int | None, date_from: str, date_to: str
    ) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT served_on, meal, location_id,
                   SUM(attending)     AS declared_attending,
                   SUM(1 - attending) AS declared_absent,
                   COUNT(*)           AS responses
            FROM attendance_intent
            WHERE served_on >= ? AND served_on <= ?
              AND (? IS NULL OR location_id = ?)
            GROUP BY served_on, meal, location_id
            ORDER BY served_on, meal, location_id
            """,
            (date_from, date_to, location_id, location_id),
        ).fetchall()
        return [dict(r) for r in rows]


    def set_stock(self, location_id: int, recipe_id: int, status: str,
                  note: str | None, user_id: str, marked_on: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO stock_item
                    (location_id, recipe_id, status, note, marked_on, marked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, recipe_id) DO UPDATE SET
                    status    = excluded.status,
                    note      = excluded.note,
                    marked_on = excluded.marked_on,
                    marked_at = excluded.marked_at
                """,
                (location_id, recipe_id, status, note, marked_on, now),
            )
            self._conn.execute(
                "INSERT INTO stock_event "
                "(location_id, recipe_id, action, note, user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (location_id, recipe_id, status, note, user_id, now),
            )
            self._conn.commit()

    def clear_stock(self, location_id: int, recipe_id: int, user_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM stock_item WHERE location_id = ? AND recipe_id = ?",
                (location_id, recipe_id),
            )
            if cur.rowcount:
                self._conn.execute(
                    "INSERT INTO stock_event "
                    "(location_id, recipe_id, action, note, user_id, created_at) "
                    "VALUES (?, ?, 'restocked', NULL, ?, ?)",
                    (location_id, recipe_id, user_id,
                     datetime.utcnow().isoformat()),
                )
            self._conn.commit()
            return cur.rowcount == 1

    def stock_marks(self, location_id: int, marked_on: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT recipe_id, status, note, marked_at FROM stock_item "
            "WHERE location_id = ? AND marked_on = ? "
            "ORDER BY marked_at DESC",
            (location_id, marked_on),
        ).fetchall()
        return [dict(r) for r in rows]

    def waste_report(
        self, location_id: int | None, date_from: str, date_to: str
    ) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT recipe_id, location_id,
                   COUNT(*)                  AS services,
                   ROUND(SUM(prepared_lb), 1) AS prepared_lb,
                   ROUND(SUM(wasted_lb), 1)   AS wasted_lb,
                   ROUND(SUM(wasted_lb) / NULLIF(SUM(prepared_lb), 0.0), 4)
                                              AS waste_rate
            FROM waste_observation
            WHERE served_on >= ? AND served_on <= ?
              AND (? IS NULL OR location_id = ?)
            GROUP BY recipe_id, location_id
            ORDER BY waste_rate DESC, recipe_id
            """,
            (date_from, date_to, location_id, location_id),
        ).fetchall()
        return [dict(r) for r in rows]
