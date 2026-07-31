from __future__ import annotations

import argparse
import sys

from app import reports
from app.config import DB_PATH
from app.store import build_store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    mint = sub.add_parser("mint", help="create a key and print it once")
    mint.add_argument("--label", required=True,
                      help='who holds it, e.g. "HUDS pilot" or "forecasting group"')
    mint.add_argument("--scopes", default="reports:read",
                      help="space-separated scopes (default: reports:read)")

    sub.add_parser("list", help="every key's metadata, never the keys themselves")

    revoke = sub.add_parser("revoke", help="disable a key by id")
    revoke.add_argument("--id", type=int, required=True)

    args = ap.parse_args()
    store = build_store()
    store.init_schema()

    if args.cmd == "mint":
        token, digest = reports.mint_key()
        key_id = store.create_report_key(args.label, args.scopes, digest)
        print(f"  key id:  {key_id}   ({args.label}, scopes: {args.scopes})")
        print(f"  db:      {DB_PATH}")
        print(f"\n  {token}\n")
        print("  This is the only time the key is shown. Store it in the")
        print("  integration's secret configuration, never in a repository.")
        return 0

    if args.cmd == "list":
        rows = store.report_keys()
        if not rows:
            print(f"no report keys in {DB_PATH}")
            return 0
        for r in rows:
            state = f"REVOKED {r['revoked_at']}" if r["revoked_at"] else "live"
            used = r["last_used_at"] or "never used"
            print(f"  #{r['id']}  {r['label']!r}  [{r['scopes']}]  "
                  f"{state}  created {r['created_at']}  last used: {used}")
        return 0

    if args.cmd == "revoke":
        if store.revoke_report_key(args.id):
            print(f"  key #{args.id} revoked. Existing pulls stop immediately.")
            return 0
        print(f"  key #{args.id} not found, or already revoked.")
        return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
