from __future__ import annotations

import argparse
import datetime as dt
import os
from zoneinfo import ZoneInfo

from app import push
from app.reference.houses import HOUSES
from app.reference.meals import DEFAULT_MEAL_NAMES, SERVICE_END
from app.store import build_store

HALL_NAMES = {h.location_id: h.location_name for h in HOUSES}
HALL_NAMES[30] = "Annenberg Hall"


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def compose(store, served_on: str, meal: int) -> list[tuple[str, str]]:
    label = DEFAULT_MEAL_NAMES.get(meal, f"Meal {meal}")
    out = []
    for row in store.feedback_counts(served_on, meal):
        name = HALL_NAMES.get(row["location_id"], f"Hall {row['location_id']}")
        body = (f"{label} feedback at {name}: {_plural(row['notes'], 'note')} "
                f"on {_plural(row['dishes'], 'dish', 'dishes')}.")
        out.append((body, f"/?pane=feedback&hall={row['location_id']}"))
    return out


def pick_meal(now: dt.datetime) -> int | None:
    for meal, end in SERVICE_END.items():
        ended = now.replace(hour=end.hour, minute=end.minute,
                            second=0, microsecond=0)
        if dt.timedelta(0) <= now - ended <= dt.timedelta(hours=1):
            return meal
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meal", type=int, default=None,
                    help="service period override (default: just-ended)")
    ap.add_argument("--date", default=None,
                    help="service date override (default: today, Boston)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the digests instead of sending")
    args = ap.parse_args()

    tz = ZoneInfo(os.environ.get("VERITASTE_TIMEZONE", "America/New_York"))
    now = dt.datetime.now(tz)
    served_on = args.date or now.date().isoformat()
    meal = args.meal if args.meal is not None else pick_meal(now)
    if meal is None:
        print("No service ended within the last hour; nothing to do.")
        return 0

    store = build_store()
    messages = compose(store, served_on, meal)
    if not messages:
        print(f"No notes for meal {meal} on {served_on}; no digest.")
        return 0

    subs = store.push_subs(affiliation="staff")
    print(f"{len(messages)} digest(s) for {len(subs)} staff subscription(s):")
    sent = 0
    for body, url in messages:
        print(f"  {body}")
        if args.dry_run:
            continue
        for sub in subs:
            ok, status, reason = push.send(
                sub.as_subscription_info(), "Veritaste", body, url)
            if status in push.DEAD_STATUSES:
                store.delete_push_sub(sub.endpoint)
            if ok:
                sent += 1
    print("Dry run — nothing sent." if args.dry_run else f"sent: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
