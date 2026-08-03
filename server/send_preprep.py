from __future__ import annotations

import argparse
import datetime as dt
import os
from zoneinfo import ZoneInfo

from app import push
from app.reference.houses import HOUSES
from app.store import build_store

HALL_NAMES = {h.location_id: h.location_name for h in HOUSES}
HALL_NAMES[30] = "Annenberg Hall"

TOP_N = 3


def armed() -> bool:
    return os.environ.get("VERITASTE_PREPREP", "").lower() in (
        "1", "on", "true", "yes")


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def compose(store, yesterday: str) -> list[tuple[str, str]]:
    out = []
    for loc, name in HALL_NAMES.items():
        rows = [r for r in store.top_wasted(loc, yesterday, TOP_N)]
        if not rows:
            continue
        body = (f"Yesterday's waste at {name}: "
                f"{_plural(len(rows), 'item')} need"
                f"{'s' if len(rows) == 1 else ''} a decision before prep.")
        out.append((body, f"/?pane=forecast&hall={loc}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="the 'yesterday' to report on (default: yesterday, "
                         "Boston)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the alerts instead of sending")
    args = ap.parse_args()

    if not armed() and not args.dry_run:
        print("VERITASTE_PREPREP is not set — built dark, nothing sent.")
        return 0

    tz = ZoneInfo(os.environ.get("VERITASTE_TIMEZONE", "America/New_York"))
    yesterday = args.date or (dt.datetime.now(tz).date()
                              - dt.timedelta(days=1)).isoformat()

    store = build_store()
    messages = compose(store, yesterday)
    if not messages:
        print(f"No waste recorded on {yesterday}; nothing to send.")
        return 0

    subs = store.push_subs(affiliation="staff")
    print(f"{len(messages)} alert(s) for {len(subs)} staff subscription(s):")
    sent = 0
    for body, url in messages:
        print(f"  {body}")
        if args.dry_run:
            continue
        for sub in subs:
            ok, status, _reason = push.send(
                sub.as_subscription_info(), "Veritaste", body, url)
            if status in push.DEAD_STATUSES:
                store.delete_push_sub(sub.endpoint)
            if ok:
                sent += 1
    print("Dry run — nothing sent." if args.dry_run else f"sent: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
