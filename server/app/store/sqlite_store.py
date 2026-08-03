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

-- One grill station per hall. `state` is the staff lever (accepting,
-- backed_up, closed); the heartbeat is stamped by every station poll and is
-- what the dead-man's switch reads. A station that has never polled is
-- indistinguishable from a dead screen. Rows are created CLOSED: plugging
-- the tablet in wakes the screen, and the Accepting tap — a deliberate
-- staff act — is what opens ordering. (First-touch used to be 'accepting',
-- which meant browsing halls on the station pane opened every hall the
-- dropdown visited; found live 2026-08-02.)
CREATE TABLE IF NOT EXISTS grill_station (
    location_id  INTEGER PRIMARY KEY,
    state        TEXT    NOT NULL CHECK (state IN ('accepting','paused','closed')),
    app_cap      INTEGER NOT NULL,
    heartbeat_at TEXT,
    updated_at   TEXT    NOT NULL
);

-- Grill orders: one main plus condiments, both live menu items. The status
-- ladder is placed -> seen -> cooking -> ready -> collected, with cancelled a
-- terminal branch; each rung stamps its own column so the record doubles as a
-- timing dataset (cooking->ready spans feed the wait estimate).
CREATE TABLE IF NOT EXISTS grill_order (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id   INTEGER NOT NULL,
    user_id       TEXT    NOT NULL,
    pickup_name   TEXT,
    main_id       INTEGER NOT NULL,
    main_name     TEXT    NOT NULL,
    condiments    TEXT    NOT NULL,
    status        TEXT    NOT NULL CHECK
        (status IN ('placed','seen','cooking','ready','collected','cancelled')),
    placed_at     TEXT    NOT NULL,
    seen_at       TEXT,
    cooking_at    TEXT,
    ready_at      TEXT,
    collected_at  TEXT,
    cancelled_at  TEXT,
    cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_grill_open ON grill_order(location_id, status);
CREATE INDEX IF NOT EXISTS idx_grill_user ON grill_order(user_id, status);

-- Menu feedback: the app's one sanctioned free-text channel — student words
-- to kitchen humans, never to reports. Latest note per (student, dish, hall,
-- service) wins; every accepted version also lands in the history table.
-- signed_name is captured at write time because a demo session's subject
-- cannot be resolved to a name after the session dies.
-- `id` is a REAL integer primary key, not an implicit rowid: the moderation
-- table (feedback_block.note_id) points at these rows, and SQLite may
-- renumber bare rowids on VACUUM — which could have silently repointed a
-- block at a different student's note, the exact misattribution class the
-- per-note re-key exists to prevent (review finding, fixed 2026-08-02;
-- migrate_feedback_rowid.py rebuilt existing databases preserving ids).
-- The old composite PK survives as the UNIQUE constraint the upsert targets.
CREATE TABLE IF NOT EXISTS menu_feedback (
    id          INTEGER PRIMARY KEY,
    user_id     TEXT    NOT NULL,
    recipe_id   INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    served_on   TEXT    NOT NULL,
    meal        INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    signed      INTEGER NOT NULL DEFAULT 0,
    signed_name TEXT,
    source      TEXT    NOT NULL DEFAULT 'sheet',
    edited      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (user_id, recipe_id, location_id, served_on, meal)
);
CREATE INDEX IF NOT EXISTS idx_feedback_hall
    ON menu_feedback(location_id, served_on);
CREATE TABLE IF NOT EXISTS menu_feedback_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    recipe_id   INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    served_on   TEXT    NOT NULL,
    meal        INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    signed      INTEGER NOT NULL,
    source      TEXT    NOT NULL,
    at          TEXT    NOT NULL
);
-- Feedback moderation: one row per staff ACTION through one note — never
-- keyed on the author across notes, because any per-author projection onto
-- the inbox is a correlation channel that deanonymizes (found 2026-08-01:
-- a user-keyed chip lit up every note an author had written). The author
-- is paused while ANY active row points at them; the chip renders only on
-- the note acted through. Per-note rows update in place so the latest
-- transition stays auditable. Blocking is a staff act, never automatic.
CREATE TABLE IF NOT EXISTS feedback_block (
    note_id      INTEGER NOT NULL PRIMARY KEY,
    user_id      TEXT    NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    reason       TEXT,
    blocked_by   TEXT    NOT NULL,
    blocked_at   TEXT    NOT NULL,
    unblocked_by TEXT,
    unblocked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_block_user
    ON feedback_block(user_id, active);

-- Staff line reports: append-only, the first real signal behind the line
-- pillar. A hall that has EVER reported retires its simulation permanently
-- (ruled 2026-08-01: real or nothing). Reports hard-expire ~30 minutes out —
-- a stale "short line" is worse than silence. A NULL band is a retraction.
CREATE TABLE IF NOT EXISTS line_report (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    band        TEXT CHECK (band IN ('no_wait','short','long') OR band IS NULL),
    user_id     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_line_report_loc
    ON line_report(location_id, id);

-- What a student would do with a dish they saw but did not eat: Would try /
-- Would skip. One ACTIVE intent per student per dish per hall; the latest tap
-- replaces it and the history table below keeps every change. A star rating
-- outranks an intent — you cannot intend a dish you have rated, and the
-- aggregates skip students who have since rated. Freshness is a read-side
-- filter, never a deletion: old intents stop counting because demand is a
-- question about now, but the record of them stays.
CREATE TABLE IF NOT EXISTS dish_intent (
    user_id     TEXT    NOT NULL,
    recipe_id   INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    intent      TEXT    NOT NULL CHECK (intent IN ('try','skip')),
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, recipe_id, location_id)
);
CREATE INDEX IF NOT EXISTS idx_dish_intent
    ON dish_intent(location_id, recipe_id, updated_at);
CREATE TABLE IF NOT EXISTS dish_intent_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    recipe_id   INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    intent      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

-- Staff-pushed rating-queue picks: "The kitchen wants your take." One active
-- pick per dish per hall, standing until staff take it back out. `picked_by`
-- is the audit trail and never reaches students (chef's-tool rule).
CREATE TABLE IF NOT EXISTS queue_pick (
    location_id INTEGER NOT NULL,
    recipe_id   INTEGER NOT NULL,
    picked_by   TEXT    NOT NULL,
    picked_at   TEXT    NOT NULL,
    PRIMARY KEY (location_id, recipe_id)
);

-- Kitchen-unlock attempts, one row per client address, shared by every
-- worker. Replaces a per-process global slot that both doubled the intended
-- rate and let one hostile client lock every legitimate staff member out —
-- and would have serialized a demo audience behind a single window. Rows
-- past the throttle window are pruned on every attempt, so this table
-- holds at most a few seconds of history and never accumulates addresses.
CREATE TABLE IF NOT EXISTS unlock_attempt (
    ip      TEXT PRIMARY KEY,
    last_at REAL NOT NULL
);

-- Staff removals from the survey: a veto keeps an auto-qualified dish out of
-- a hall's survey until staff add it back — a pick clears its veto. Without
-- this, removing a dish the system chose just re-chose it on the next load:
-- mechanically honest, humanly baffling (ruled 2026-08-02).
CREATE TABLE IF NOT EXISTS queue_veto (
    location_id INTEGER NOT NULL,
    recipe_id   INTEGER NOT NULL,
    vetoed_by   TEXT    NOT NULL,
    vetoed_at   TEXT    NOT NULL,
    PRIMARY KEY (location_id, recipe_id)
);
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
            self._ensure_column("grill_order", "pickup_name", "TEXT")
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

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
                    (user_id, recipe_id, location_id, score, served_on,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, recipe_id, location_id) DO UPDATE SET
                    score      = excluded.score,
                    served_on  = excluded.served_on,
                    updated_at = excluded.updated_at
                """,
                (user_id, recipe_id, location_id, score, served_on, now, now),
            )
            self._conn.execute(
                "INSERT INTO rating_history "
                "(user_id, recipe_id, location_id, score, served_on, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, recipe_id, location_id, score, served_on, now),
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

    def delete_push_sub_for_user(self, endpoint: str, user_sub: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM push_subscription WHERE endpoint = ? "
                "AND user_sub = ?",
                (endpoint, user_sub),
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


    def upsert_feedback(self, user_id: str, recipe_id: int, location_id: int,
                        served_on: str, meal: int, text: str, signed: bool,
                        signed_name: str | None, source: str) -> bool:
        now = datetime.utcnow().isoformat()
        with self._lock:
            prior = self._conn.execute(
                "SELECT 1 FROM menu_feedback WHERE user_id = ? AND "
                "recipe_id = ? AND location_id = ? AND served_on = ? "
                "AND meal = ?",
                (user_id, recipe_id, location_id, served_on, meal),
            ).fetchone() is not None
            self._conn.execute(
                """
                INSERT INTO menu_feedback
                    (user_id, recipe_id, location_id, served_on, meal,
                     text, signed, signed_name, source, edited,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(user_id, recipe_id, location_id, served_on, meal)
                DO UPDATE SET
                    text        = excluded.text,
                    signed      = excluded.signed,
                    signed_name = excluded.signed_name,
                    source      = excluded.source,
                    edited      = 1,
                    updated_at  = excluded.updated_at
                """,
                (user_id, recipe_id, location_id, served_on, meal,
                 text, int(signed), signed_name, source, now, now),
            )
            self._conn.execute(
                "INSERT INTO menu_feedback_history "
                "(user_id, recipe_id, location_id, served_on, meal, text, "
                "signed, source, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, recipe_id, location_id, served_on, meal,
                 text, int(signed), source, now),
            )
            self._conn.commit()
        return prior

    def feedback_for_location(self, location_id: int,
                              served_on: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT f.id, f.recipe_id, f.meal, f.text,
                   CASE WHEN f.signed = 1 THEN f.signed_name END AS signed_name,
                   f.source, f.edited, f.created_at, f.updated_at,
                   CASE WHEN b.note_id IS NOT NULL
                        THEN 1 ELSE 0 END AS blocked,
                   (SELECT score FROM rating r
                     WHERE r.user_id = f.user_id
                       AND r.recipe_id = f.recipe_id
                       AND r.location_id = f.location_id) AS rating_score
            FROM menu_feedback f
            LEFT JOIN feedback_block b
                   ON b.note_id = f.id AND b.active = 1
            WHERE f.location_id = ? AND f.served_on = ?
            ORDER BY f.updated_at DESC
            """,
            (location_id, served_on),
        ).fetchall()
        return [dict(r) for r in rows]

    def feedback_author(self, note_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT user_id FROM menu_feedback WHERE id = ?", (note_id,),
        ).fetchone()
        return row["user_id"] if row else None

    def feedback_of(self, user_id: str, recipe_id: int, location_id: int,
                    served_on: str, meal: int) -> dict | None:
        row = self._conn.execute(
            "SELECT text, signed, source, edited, updated_at "
            "FROM menu_feedback WHERE user_id = ? AND recipe_id = ? AND "
            "location_id = ? AND served_on = ? AND meal = ?",
            (user_id, recipe_id, location_id, served_on, meal),
        ).fetchone()
        return dict(row) if row else None

    def feedback_counts(self, served_on: str, meal: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT location_id, COUNT(*) AS notes, "
            "COUNT(DISTINCT recipe_id) AS dishes "
            "FROM menu_feedback WHERE served_on = ? AND meal = ? "
            "GROUP BY location_id ORDER BY location_id",
            (served_on, meal),
        ).fetchall()
        return [dict(r) for r in rows]


    def add_line_report(self, location_id: int, band: str | None,
                        user_id: str, expires_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO line_report "
                "(location_id, band, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (location_id, band, user_id,
                 datetime.utcnow().isoformat(), expires_at),
            )
            self._conn.commit()

    def latest_line_report(self, location_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT band, created_at, expires_at FROM line_report "
            "WHERE location_id = ? ORDER BY id DESC LIMIT 1",
            (location_id,),
        ).fetchone()
        return dict(row) if row else None

    def line_report_history(self, location_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT band, created_at FROM line_report "
            "WHERE location_id = ? AND band IS NOT NULL ORDER BY id",
            (location_id,),
        ).fetchall()
        return [dict(r) for r in rows]


    def attendance_baseline(self, location_id: int, sql_dow: str, meal: int,
                            before: str) -> tuple[float | None, int]:
        rows = self._conn.execute(
            """
            SELECT SUM(CASE WHEN attending = 1 THEN 1 ELSE 0 END) AS yes
            FROM attendance_intent
            WHERE location_id = ? AND meal = ? AND served_on < ?
              AND strftime('%w', served_on) = ?
            GROUP BY served_on ORDER BY served_on DESC LIMIT 8
            """,
            (location_id, meal, before, sql_dow),
        ).fetchall()
        if not rows:
            return None, 0
        return sum(r["yes"] for r in rows) / len(rows), len(rows)

    def rated_extremes(self, location_id: int, limit: int) -> dict:
        def _q(order: str) -> list[dict]:
            return [dict(r) for r in self._conn.execute(
                f"""
                SELECT recipe_id, ROUND(AVG(score), 2) AS average,
                       COUNT(*) AS count
                FROM rating WHERE location_id = ?
                GROUP BY recipe_id
                ORDER BY AVG(score) {order}, COUNT(*) DESC
                LIMIT ?
                """,
                (location_id, limit),
            ).fetchall()]
        return {"top": _q("DESC"), "bottom": _q("ASC")}

    def top_wasted(self, location_id: int, since: str, limit: int) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            """
            SELECT recipe_id, ROUND(SUM(wasted_lb), 1) AS wasted_lb,
                   MAX(source) AS source
            FROM waste_observation
            WHERE location_id = ? AND served_on >= ?
            GROUP BY recipe_id ORDER BY SUM(wasted_lb) DESC LIMIT ?
            """,
            (location_id, since, limit),
        ).fetchall()]


    def throttle_unlock(self, ip: str, window_s: int) -> int:
        import time as _time
        now = _time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT last_at FROM unlock_attempt WHERE ip = ?", (ip,),
            ).fetchone()
            if row is not None:
                wait = window_s - (now - row["last_at"])
                if wait > 0:
                    self._conn.commit()
                    return int(wait) + 1
            self._conn.execute(
                "INSERT INTO unlock_attempt (ip, last_at) VALUES (?, ?) "
                "ON CONFLICT(ip) DO UPDATE SET last_at = excluded.last_at",
                (ip, now))
            self._conn.execute(
                "DELETE FROM unlock_attempt WHERE last_at < ?",
                (now - window_s,))
            self._conn.commit()
        return 0


    def attendance_yes_users(self, location_id: int, served_on: str,
                             meal: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT user_id FROM attendance_intent WHERE location_id = ? "
            "AND served_on = ? AND meal = ? AND attending = 1",
            (location_id, served_on, meal),
        ).fetchall()
        return [r["user_id"] for r in rows]

    def grill_order_users(self, location_id: int, since: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT user_id FROM grill_order WHERE location_id = ? "
            "AND placed_at >= ? AND status != 'cancelled'",
            (location_id, since),
        ).fetchall()
        return {r["user_id"] for r in rows}


    def set_dish_intent(self, user_id: str, recipe_id: int, location_id: int,
                        intent: str | None) -> None:
        now = datetime.utcnow().isoformat()
        with self._lock:
            if intent is None:
                self._conn.execute(
                    "DELETE FROM dish_intent WHERE user_id = ? AND "
                    "recipe_id = ? AND location_id = ?",
                    (user_id, recipe_id, location_id))
            else:
                self._conn.execute(
                    """
                    INSERT INTO dish_intent
                        (user_id, recipe_id, location_id, intent, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, recipe_id, location_id) DO UPDATE SET
                        intent     = excluded.intent,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, recipe_id, location_id, intent, now))
            self._conn.execute(
                "INSERT INTO dish_intent_history "
                "(user_id, recipe_id, location_id, intent, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, recipe_id, location_id, intent or "cleared", now))
            self._conn.commit()

    def dish_intents_for(self, user_id: str, recipe_ids: list[int],
                         location_id: int, since: str) -> dict[int, str]:
        if not recipe_ids:
            return {}
        rows = self._conn.execute(
            f"SELECT recipe_id, intent FROM dish_intent "
            f"WHERE user_id = ? AND location_id = ? AND updated_at >= ? "
            f"AND recipe_id IN ({_placeholders(len(recipe_ids))})",
            (user_id, location_id, since, *recipe_ids),
        ).fetchall()
        return {r["recipe_id"]: r["intent"] for r in rows}

    def intent_counts(self, location_id: int, recipe_ids: list[int],
                      since: str) -> dict[int, dict]:
        if not recipe_ids:
            return {}
        rows = self._conn.execute(
            f"""
            SELECT i.recipe_id, i.intent, COUNT(*) AS n
            FROM dish_intent i
            WHERE i.location_id = ? AND i.updated_at >= ?
              AND i.recipe_id IN ({_placeholders(len(recipe_ids))})
              AND NOT EXISTS (
                  SELECT 1 FROM rating r
                  WHERE r.user_id = i.user_id
                    AND r.recipe_id = i.recipe_id
                    AND r.location_id = i.location_id)
            GROUP BY i.recipe_id, i.intent
            """,
            (location_id, since, *recipe_ids),
        ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            out.setdefault(r["recipe_id"], {"try": 0, "skip": 0})[r["intent"]] = r["n"]
        return out

    def has_rating(self, user_id: str, recipe_id: int,
                   location_id: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM rating WHERE user_id = ? AND recipe_id = ? AND "
            "location_id = ? LIMIT 1",
            (user_id, recipe_id, location_id),
        ).fetchone() is not None

    def user_ratings_for(self, user_id: str, recipe_ids: list[int],
                         location_id: int) -> dict[int, int]:
        if not recipe_ids:
            return {}
        rows = self._conn.execute(
            f"SELECT recipe_id, score FROM rating WHERE user_id = ? AND "
            f"location_id = ? AND recipe_id IN "
            f"({_placeholders(len(recipe_ids))})",
            (user_id, location_id, *recipe_ids),
        ).fetchall()
        return {r["recipe_id"]: r["score"] for r in rows}

    def queue_picks(self, location_id: int) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT recipe_id, picked_at FROM queue_pick "
            "WHERE location_id = ? ORDER BY picked_at DESC, recipe_id",
            (location_id,),
        ).fetchall()]

    def add_queue_pick(self, location_id: int, recipe_id: int,
                       user_id: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO queue_pick
                    (location_id, recipe_id, picked_by, picked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(location_id, recipe_id) DO UPDATE SET
                    picked_by = excluded.picked_by,
                    picked_at = excluded.picked_at
                """,
                (location_id, recipe_id, user_id, now))
            self._conn.execute(
                "DELETE FROM queue_veto WHERE location_id = ? AND "
                "recipe_id = ?",
                (location_id, recipe_id))
            self._conn.commit()

    def remove_queue_pick(self, location_id: int, recipe_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM queue_pick WHERE location_id = ? AND recipe_id = ?",
                (location_id, recipe_id))
            self._conn.commit()
        return cur.rowcount > 0

    def queue_vetoes(self, location_id: int) -> set[int]:
        rows = self._conn.execute(
            "SELECT recipe_id FROM queue_veto WHERE location_id = ?",
            (location_id,),
        ).fetchall()
        return {r["recipe_id"] for r in rows}

    def add_queue_veto(self, location_id: int, recipe_id: int,
                       user_id: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO queue_veto
                    (location_id, recipe_id, vetoed_by, vetoed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(location_id, recipe_id) DO UPDATE SET
                    vetoed_by = excluded.vetoed_by,
                    vetoed_at = excluded.vetoed_at
                """,
                (location_id, recipe_id, user_id, now))
            self._conn.commit()

    def feedback_blocked(self, user_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM feedback_block WHERE user_id = ? AND active = 1 "
            "LIMIT 1", (user_id,),
        ).fetchone()
        return row is not None

    def set_feedback_block(self, note_id: int, user_id: str, blocked_by: str,
                           reason: str | None) -> None:
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO feedback_block
                    (note_id, user_id, active, reason, blocked_by, blocked_at)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    active     = 1,
                    reason     = excluded.reason,
                    blocked_by = excluded.blocked_by,
                    blocked_at = excluded.blocked_at
                """,
                (note_id, user_id, reason, blocked_by, now),
            )
            self._conn.commit()

    def clear_feedback_block(self, note_id: int, unblocked_by: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE feedback_block SET active = 0, unblocked_by = ?, "
                "unblocked_at = ? WHERE note_id = ? AND active = 1",
                (unblocked_by, datetime.utcnow().isoformat(), note_id),
            )
            self._conn.commit()
            return cur.rowcount == 1


    def grill_station(self, location_id: int, default_cap: int) -> dict:
        row = self._conn.execute(
            "SELECT location_id, state, app_cap, heartbeat_at, updated_at "
            "FROM grill_station WHERE location_id = ?", (location_id,),
        ).fetchone()
        if row is not None:
            return dict(row)
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO grill_station "
                "(location_id, state, app_cap, heartbeat_at, updated_at) "
                "VALUES (?, 'closed', ?, NULL, ?)",
                (location_id, default_cap, now),
            )
            self._conn.commit()
        return self.grill_station(location_id, default_cap)

    def set_grill_station(self, location_id: int, default_cap: int,
                          state: str | None = None,
                          app_cap: int | None = None) -> dict:
        self.grill_station(location_id, default_cap)
        sets, params = ["updated_at = ?"], [datetime.utcnow().isoformat()]
        if state is not None:
            sets.append("state = ?")
            params.append(state)
        if app_cap is not None:
            sets.append("app_cap = ?")
            params.append(app_cap)
        params.append(location_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE grill_station SET {', '.join(sets)} "
                "WHERE location_id = ?", params,
            )
            self._conn.commit()
        return self.grill_station(location_id, default_cap)

    def grill_poll(self, location_id: int, default_cap: int) -> tuple[dict, list[dict]]:
        now = datetime.utcnow().isoformat()
        self.grill_station(location_id, default_cap)
        with self._lock:
            self._conn.execute(
                "UPDATE grill_station SET heartbeat_at = ? WHERE location_id = ?",
                (now, location_id),
            )
            self._conn.execute(
                "UPDATE grill_order SET status = 'seen', seen_at = ? "
                "WHERE location_id = ? AND status = 'placed'",
                (now, location_id),
            )
            self._conn.commit()
        orders = self._conn.execute(
            "SELECT * FROM grill_order WHERE location_id = ? "
            "AND status IN ('seen','cooking','ready') "
            "ORDER BY placed_at, id", (location_id,),
        ).fetchall()
        return self.grill_station(location_id, default_cap), [dict(r) for r in orders]

    def grill_open_orders(self, location_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM grill_order WHERE location_id = ? "
            "AND status IN ('placed','seen','cooking','ready') "
            "ORDER BY placed_at, id", (location_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def open_app_count(self, location_id: int) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM grill_order WHERE location_id = ? "
            "AND status IN ('placed','seen','cooking')", (location_id,),
        ).fetchone()[0]

    def user_open_grill_order(self, user_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM grill_order WHERE user_id = ? "
            "AND status IN ('placed','seen','cooking','ready') "
            "ORDER BY id DESC LIMIT 1", (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def place_grill_order(self, location_id: int, user_id: str, main_id: int,
                          main_name: str, condiments_json: str,
                          pickup_name: str) -> dict:
        now = datetime.utcnow().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO grill_order (location_id, user_id, pickup_name, "
                "main_id, main_name, condiments, status, placed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'placed', ?)",
                (location_id, user_id, pickup_name, main_id, main_name,
                 condiments_json, now),
            )
            self._conn.commit()
        return dict(self._conn.execute(
            "SELECT * FROM grill_order WHERE id = ?", (cur.lastrowid,)
        ).fetchone())

    def get_grill_order(self, order_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM grill_order WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    _GRILL_ADVANCES = {
        "cooking": ("placed", "seen"),
        "ready": ("cooking",),
        "collected": ("ready",),
    }

    def advance_grill_order(self, order_id: int, to: str) -> dict | None:
        allowed = self._GRILL_ADVANCES.get(to)
        if not allowed:
            return None
        assert to.isidentifier()
        now = datetime.utcnow().isoformat()
        marks = ",".join("?" * len(allowed))
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE grill_order SET status = ?, {to}_at = ? "
                f"WHERE id = ? AND status IN ({marks})",
                (to, now, order_id, *allowed),
            )
            self._conn.commit()
        if cur.rowcount != 1:
            return None
        return self.get_grill_order(order_id)

    def cancel_grill_order(self, order_id: int, reason: str,
                           by_staff: bool) -> dict | None:
        allowed = ("placed", "seen", "cooking", "ready") if by_staff \
            else ("placed", "seen")
        marks = ",".join("?" * len(allowed))
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE grill_order SET status = 'cancelled', "
                f"cancelled_at = ?, cancel_reason = ? "
                f"WHERE id = ? AND status IN ({marks})",
                (datetime.utcnow().isoformat(), reason, order_id, *allowed),
            )
            self._conn.commit()
        if cur.rowcount != 1:
            return None
        return self.get_grill_order(order_id)

    def grill_cook_estimate_s(self, location_id: int, default_s: int) -> int:
        rows = self._conn.execute(
            "SELECT (julianday(ready_at) - julianday(cooking_at)) * 86400 AS s "
            "FROM grill_order WHERE location_id = ? AND ready_at IS NOT NULL "
            "AND cooking_at IS NOT NULL ORDER BY id DESC LIMIT 5",
            (location_id,),
        ).fetchall()
        spans = [r["s"] for r in rows if r["s"] and r["s"] > 0]
        if not spans:
            return default_s
        return int(sum(spans) / len(spans))

    def grill_orders_containing(self, location_id: int, recipe_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT o.* FROM grill_order o "
            "WHERE o.location_id = ? "
            "AND o.status IN ('placed','seen','cooking') "
            "AND (o.main_id = ? OR EXISTS ("
            "  SELECT 1 FROM json_each(o.condiments) j "
            "  WHERE json_extract(j.value, '$.id') = ?))",
            (location_id, recipe_id, recipe_id),
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
