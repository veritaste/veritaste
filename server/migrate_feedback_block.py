from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys
from pathlib import Path

NEW_DDL = """
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
"""


def table_key(conn: sqlite3.Connection) -> str | None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(feedback_block)")}
    if not cols:
        return None
    return "note_id" if "note_id" in cols else "user_id"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually rebuild the table (default: dry run)")
    args = ap.parse_args()

    db = os.environ.get("VERITASTE_DB") or str(
        Path(__file__).resolve().parent / "veritaste.db")
    if not Path(db).exists():
        print(f"No database at {db} (set VERITASTE_DB).")
        return 2
    print(f"database: {db}")

    conn = sqlite3.connect(db)
    key = table_key(conn)
    if key is None:
        print("feedback_block does not exist yet; nothing to rebuild.")
        return 0
    if key == "note_id":
        print("feedback_block already keyed per note; nothing to do.")
        return 0
    n = conn.execute("SELECT COUNT(*) FROM feedback_block").fetchone()[0]
    print(f"feedback_block: old author-keyed shape, {n} row(s) "
          "(dropped by the rebuild — affected students unpause)")
    if not args.apply:
        print("\nDry run — nothing changed. Rerun with --apply.")
        return 0
    conn.close()

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = str(Path(db).with_name(
        f"{Path(db).stem}-preblockrekey-{stamp}.db"))
    shutil.copy2(db, backup)
    print(f"backup: {backup}")

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE feedback_block")
    conn.executescript(NEW_DDL)
    conn.commit()
    conn.close()
    print("feedback_block rebuilt, keyed per note.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
