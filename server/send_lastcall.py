from __future__ import annotations

import argparse
import datetime as dt
import os
from zoneinfo import ZoneInfo

from app import push
from app.reference.houses import HOUSES
from app.reference.meals import SERVICE_END, meal_name, service_ends_at
from app.signals.swipes import NoSwipeFeed, SwipePresenceProvider
from app.store import build_store

HALL_NAMES = {h.location_id: h.location_name for h in HOUSES}
HALL_NAMES[30] = "Annenberg Hall"
HALL_NAMES[29] = "Fly-By"

WINDOW = dt.timedelta(minutes=35)


def armed() -> bool:
    return os.environ.get("VERITASTE_LASTCALL", "").lower() in (
        "1", "on", "true", "yes")


def ending_services(now: dt.datetime, tz: ZoneInfo) -> list[tuple[int, int]]:
    out = []
    for loc in HALL_NAMES:
        for meal in SERVICE_END:
            end = service_ends_at(loc, now.date(), meal, tz)
            if end is not None and dt.timedelta(0) < end - now <= WINDOW:
                out.append((loc, meal))
    return out


def compose(store, services: list[tuple[int, int]], served_on: str,
            day_start_utc: str,
            swipes: SwipePresenceProvider) -> list[tuple[list[str], str, str]]:
    out = []
    for loc, meal in services:
        targets = set(store.attendance_yes_users(loc, served_on, meal))
        if not targets:
            continue
        targets -= store.grill_order_users(loc, day_start_utc)
        try:
            targets -= swipes.present_users(loc, served_on, meal)
        except NotImplementedError:
            pass
        if not targets:
            continue
        body = f"{meal_name(loc, meal)} closes soon at {HALL_NAMES[loc]}."
        out.append((sorted(targets), body, f"/?hall={loc}&meal={meal}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meal", type=int, default=None,
                    help="service period override (default: about to end)")
    ap.add_argument("--date", default=None,
                    help="service date override (default: today, Boston)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the reminders instead of sending")
    args = ap.parse_args()

    if not armed() and not args.dry_run:
        print("VERITASTE_LASTCALL is not set — built dark, nothing sent.")
        return 0

    tz = ZoneInfo(os.environ.get("VERITASTE_TIMEZONE", "America/New_York"))
    now = dt.datetime.now(tz)
    served_on = args.date or now.date().isoformat()
    day_start_utc = (dt.datetime.combine(now.date(), dt.time.min, tzinfo=tz)
                     .astimezone(dt.timezone.utc)
                     .replace(tzinfo=None).isoformat())

    store = build_store()
    if args.meal is not None:
        services = [(loc, args.meal) for loc in HALL_NAMES]
    else:
        services = ending_services(now, tz)
    if not services:
        print("No service ends within the next half hour; nothing to do.")
        return 0

    messages = compose(store, services, served_on, day_start_utc,
                       NoSwipeFeed())
    if not messages:
        print("Nobody declared for what is ending; nothing to send.")
        return 0

    subs_by_user: dict[str, list] = {}
    for sub in store.push_subs(affiliation="student"):
        subs_by_user.setdefault(sub.user_sub, []).append(sub)

    sent = 0
    for targets, body, url in messages:
        reachable = sum(len(subs_by_user.get(u, [])) for u in targets)
        print(f"  {body}  [{len(targets)} declared, "
              f"{reachable} subscription(s)]")
        if args.dry_run:
            continue
        for user in targets:
            for sub in subs_by_user.get(user, []):
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
