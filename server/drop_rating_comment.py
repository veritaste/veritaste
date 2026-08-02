from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys
from pathlib import Path

TABLES = ("rating", "rating_history")


def has_comment(conn: sqlite3.Connection, table: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return "comment" in cols


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually alter the database (default: dry run)")
    args = ap.parse_args()

    if sqlite3.sqlite_version_info < (3, 35, 0):
        print(f"SQLite {sqlite3.sqlite_version} cannot DROP COLUMN "
              "(needs 3.35+).")
        return 2

    db = os.environ.get("VERITASTE_DB") or str(
        Path(__file__).resolve().parent / "veritaste.db")
    if not Path(db).exists():
        print(f"No database at {db} (set VERITASTE_DB).")
        return 2
    print(f"database: {db}")

    conn = sqlite3.connect(db)
    todo = [t for t in TABLES if has_comment(conn, t)]
    for t in TABLES:
        if t in todo:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {t} WHERE comment IS NOT NULL"
            ).fetchone()[0]
            print(f"{t}: comment column present, {n} non-null value(s)")
        else:
            print(f"{t}: already clean")
    if not todo:
        return 0
    if not args.apply:
        print("\nDry run — nothing changed. Rerun with --apply.")
        return 0
    conn.close()

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = str(Path(db).with_name(
        f"{Path(db).stem}-precommentdrop-{stamp}.db"))
    shutil.copy2(db, backup)
    print(f"backup: {backup}")

    conn = sqlite3.connect(db)
    for t in todo:
        conn.execute(f"ALTER TABLE {t} DROP COLUMN comment")
        print(f"{t}: comment column dropped")
    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
