from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys

from app.config import DB_PATH

NEW_SHAPE = """
CREATE TABLE menu_feedback_new (
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
)
"""

COLS = ("user_id, recipe_id, location_id, served_on, meal, text, signed, "
        "signed_name, source, edited, created_at, updated_at")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually migrate (default: dry run)")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cols = [r["name"] for r in conn.execute(
        "PRAGMA table_info(menu_feedback)")]
    if not cols:
        print(f"{DB_PATH}: no menu_feedback table; nothing to do.")
        return 0
    if "id" in cols:
        print(f"{DB_PATH}: menu_feedback already carries id; nothing to do.")
        return 0

    notes = conn.execute(
        "SELECT COUNT(*) AS c FROM menu_feedback").fetchone()["c"]
    blocks = conn.execute(
        "SELECT COUNT(*) AS c FROM feedback_block").fetchone()["c"]
    matched = conn.execute(
        "SELECT COUNT(*) AS c FROM feedback_block b WHERE EXISTS "
        "(SELECT 1 FROM menu_feedback f WHERE f.rowid = b.note_id)"
    ).fetchone()["c"]
    print(f"{DB_PATH}: {notes} note(s), {blocks} block row(s), "
          f"{matched} block(s) resolving to a note.")

    if not args.apply:
        print("Dry run — pass --apply to migrate. The rebuild copies each "
              "row's rowid as its permanent id, so blocks stay pointed at "
              "the notes they were made through.")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{DB_PATH}.premigrate-feedback-{stamp}"
    dst = sqlite3.connect(backup_path)
    with dst:
        conn.backup(dst)
    dst.close()
    print(f"backup written: {backup_path}")

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(NEW_SHAPE)
        conn.execute(
            f"INSERT INTO menu_feedback_new (id, {COLS}) "
            f"SELECT rowid, {COLS} FROM menu_feedback")
        conn.execute("DROP TABLE menu_feedback")
        conn.execute("ALTER TABLE menu_feedback_new RENAME TO menu_feedback")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_hall "
                     "ON menu_feedback(location_id, served_on)")

    after = conn.execute(
        "SELECT COUNT(*) AS c FROM menu_feedback").fetchone()["c"]
    still = conn.execute(
        "SELECT COUNT(*) AS c FROM feedback_block b WHERE EXISTS "
        "(SELECT 1 FROM menu_feedback f WHERE f.id = b.note_id)"
    ).fetchone()["c"]
    ok = after == notes and still == matched
    print(f"after: {after} note(s) (was {notes}); "
          f"{still} block(s) still resolving (was {matched}).")
    print("OK — ids are permanent now." if ok
          else "MISMATCH — restore the backup and investigate.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
