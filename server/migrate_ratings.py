from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.config import DB_PATH


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="perform the migration")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"no database at {path}; nothing to migrate")
        return

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    cols = columns(conn, "rating")
    if not cols:
        print("no `rating` table yet — a fresh database will be created correctly")
        return
    if "updated_at" in cols:
        print("already migrated (rating.updated_at present); nothing to do")
        return

    total = conn.execute("SELECT COUNT(*) FROM rating").fetchone()[0]
    keepable = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT user_id, recipe_id, location_id "
        "FROM rating WHERE location_id IS NOT NULL)"
    ).fetchone()[0]
    orphan = conn.execute(
        "SELECT COUNT(*) FROM rating WHERE location_id IS NULL"
    ).fetchone()[0]
    worst = conn.execute(
        "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM rating "
        "GROUP BY user_id, recipe_id, location_id)"
    ).fetchone()[0] or 0

    print(f"  rows now:                      {total}")
    print(f"  distinct (student, dish, hall): {keepable}")
    print(f"  rows with no hall (history only): {orphan}")
    print(f"  most votes by one student on one dish: {worst}")
    print(f"  rows that will count after migration: {keepable}")

    if not args.apply:
        print("\nreport only — re-run with --apply to perform it")
        return

    backup = path.with_name(f"{path.stem}-premigrate-"
                            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    shutil.copy2(path, backup)
    print(f"\n  backup written: {backup}")

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ALTER TABLE rating RENAME TO rating_legacy")
        conn.executescript("""
            CREATE TABLE rating (
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
        """)

        conn.execute("""
            INSERT INTO rating_history
                (user_id, recipe_id, location_id, score, served_on, comment, created_at)
            SELECT user_id, recipe_id, COALESCE(location_id, 0), score,
                   served_on, comment, created_at
            FROM rating_legacy
        """)

        conn.execute("""
            INSERT INTO rating
                (user_id, recipe_id, location_id, score, served_on, comment,
                 created_at, updated_at)
            SELECT r.user_id, r.recipe_id, r.location_id, r.score, r.served_on,
                   r.comment, first.first_at, r.created_at
            FROM rating_legacy r
            JOIN (
                SELECT user_id, recipe_id, location_id,
                       MIN(created_at) AS first_at,
                       MAX(created_at || '#' || id) AS newest
                FROM rating_legacy
                WHERE location_id IS NOT NULL
                GROUP BY user_id, recipe_id, location_id
            ) first
              ON  first.user_id     = r.user_id
              AND first.recipe_id   = r.recipe_id
              AND first.location_id = r.location_id
              AND first.newest      = r.created_at || '#' || r.id
        """)

        moved = conn.execute("SELECT COUNT(*) FROM rating").fetchone()[0]
        hist = conn.execute("SELECT COUNT(*) FROM rating_history").fetchone()[0]
        if hist != total:
            raise RuntimeError(f"history {hist} != original {total}; rolling back")
        conn.execute("DROP TABLE rating_legacy")
        conn.commit()
    except Exception:
        conn.rollback()
        print("  FAILED — rolled back; database unchanged")
        raise

    print(f"  current votes:  {moved}")
    print(f"  history rows:   {hist}  (every original row preserved)")
    print("  done")


if __name__ == "__main__":
    sys.exit(main())
