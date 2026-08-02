from __future__ import annotations

import datetime as dt
import functools
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time

from apiflask import APIFlask, Schema, abort
from apiflask.fields import Boolean, Integer, List, String
from apiflask.validators import Length, OneOf, Range
from flask import current_app, g, jsonify, request, send_from_directory

from . import __version__, push, reports
from .auth import (KITCHEN_COOKIE, KITCHEN_TTL_S, SESSION_COOKIE, User,
                   current_user, issue_kitchen, issue_session,
                   kitchen_unlocked, login_required)
from .config import (CACHE_TTL_HOURS, DB_PATH, DEMO_MODE, ECS_APIKEY,
                     GRILL_APP_CAP_DEFAULT, GRILL_COOK_S_DEFAULT,
                     GRILL_HEARTBEAT_STALE_S, GRILL_LAST_CALL_MIN,
                     GRILL_WAIT_CAP_MIN, LOCAL_TZ,
                     MODE, RATING_RECENT_DAYS, STAFF_PASSCODE, STORE_BACKEND,
                     TIMEZONE, UNLOCK_WINDOW_S, WEB_DIR)
from .reference.grill import GRILL_CATEGORY, condiment_ids_for, split_grill
from .reference.meals import service_ends_at
from .rewards import (DAILY_CAP_CENTS, DAY_SCOPED, GRANTS, LABELS,
                      format_cents, settlement_note)
from .reference import (ANNENBERG_LOCATION, BY_KEY, BY_NETID, HOUSES,
                        RETAIL_LOCATIONS, declaration_closes_at, is_brunch_day,
                        meal_name, open_dining_locations, public_list,
                        service_status, takes_attendance, where_can_i_eat)
from .signals import SimulatedLineProvider, spice_for
from .sources import MEAL_NAMES, DiningSource
from .store import PushSub, build_store

API = "/api/v1"

LINE_REPORT_TTL_MIN = 30

DISCLAIMER = (
    "This is not affiliated with or endorsed by Harvard University or HUDS."
)


class MenuQuery(Schema):
    date = String(load_default=lambda: _today())
    location = Integer(required=True)
    meal = Integer(load_default=1, validate=Range(min=0, max=2))


class LineQuery(Schema):
    date = String(load_default=lambda: _today())


class InterhouseQuery(Schema):
    date = String(load_default=lambda: _today())
    meal = Integer(load_default=2, validate=Range(min=0, max=2))
    house = String(load_default=None, allow_none=True)


class AttendanceQuery(Schema):
    location = Integer(required=True)
    date = String(load_default=lambda: _today())
    meal = Integer(load_default=2, validate=Range(min=0, max=2))


class RatingIn(Schema):
    recipe_id = Integer(required=True)
    score = Integer(required=True, validate=Range(min=1, max=5))
    location_id = Integer(required=True)
    served_on = String(load_default=None, allow_none=True)


class RecipeQuery(Schema):
    location = Integer(load_default=None, allow_none=True)
    meal = Integer(load_default=None, allow_none=True)


class AttendanceIn(Schema):
    location_id = Integer(required=True)
    meal = Integer(load_default=2, validate=Range(min=0, max=2))
    served_on = String(load_default=None, allow_none=True)
    attending = Boolean(required=True)


class SignInIn(Schema):
    netid = String(required=True, validate=OneOf(list(BY_NETID)))


class PushSubIn(Schema):
    endpoint = String(required=True, validate=Length(max=1000))
    p256dh = String(required=True, validate=Length(max=256))
    auth = String(required=True, validate=Length(max=256))


class PushEndpointIn(Schema):
    endpoint = String(required=True, validate=Length(max=1000))


class AvailabilityQuery(Schema):
    location = Integer(required=True)


class StockIn(Schema):
    location_id = Integer(required=True)
    recipe_id = Integer(required=True)
    status = String(required=True, validate=OneOf(["low", "out"]))
    note = String(load_default=None, allow_none=True, validate=Length(max=140))


class StockClearQuery(Schema):
    location = Integer(required=True)
    recipe = Integer(required=True)


class KitchenIn(Schema):
    passcode = String(required=True, validate=Length(max=200))


class FeedbackIn(Schema):
    recipe_id = Integer(required=True)
    location_id = Integer(required=True)
    meal = Integer(required=True, validate=Range(min=0))
    text = String(required=True, validate=Length(min=1, max=500))
    signed = Boolean(load_default=False)
    source = String(load_default="sheet",
                    validate=OneOf(["sheet", "rating", "survey"]))


class FeedbackQuery(Schema):
    location = Integer(required=True)
    date = String(load_default=None, allow_none=True)


class FeedbackBlockIn(Schema):
    note_id = Integer(required=True)
    reason = String(load_default=None, allow_none=True,
                    validate=Length(max=200))


class FeedbackBlockQuery(Schema):
    note_id = Integer(required=True)


class LineReportIn(Schema):
    band = String(required=True, allow_none=True,
                  validate=OneOf(["no_wait", "short", "long"]))


class ForecastQuery(Schema):
    location = Integer(required=True)
    meal = Integer(required=True, validate=Range(min=0))
    date = String(load_default=None, allow_none=True)


class GrillQuery(Schema):
    location = Integer(required=True)


class GrillOrderIn(Schema):
    location_id = Integer(required=True)
    main_id = Integer(required=True)
    condiments = List(Integer(), load_default=[])


class GrillStationIn(Schema):
    location_id = Integer(required=True)
    state = String(load_default=None, allow_none=True,
                   validate=OneOf(["accepting", "paused", "closed"]))
    app_cap = Integer(load_default=None, allow_none=True,
                      validate=Range(min=1, max=10))


class GrillAdvanceIn(Schema):
    to = String(required=True, validate=OneOf(["cooking", "ready", "collected"]))


class RatingsReportQuery(Schema):
    location = Integer(load_default=None, allow_none=True)
    days = Integer(load_default=RATING_RECENT_DAYS, validate=Range(min=1, max=365))
    format = String(load_default="json", validate=OneOf(["json", "csv"]))


class ServiceReportQuery(Schema):
    location = Integer(load_default=None, allow_none=True)
    date_from = String(data_key="from", load_default=None, allow_none=True)
    date_to = String(data_key="to", load_default=None, allow_none=True)
    format = String(load_default="json", validate=OneOf(["json", "csv"]))


def _svc():
    return current_app.extensions["veritaste"]


def _today() -> str:
    return dt.datetime.now(LOCAL_TZ).date().isoformat()


def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        abort(400, "date must be YYYY-MM-DD")


def _band(rate: float) -> str:
    if rate >= 0.90:
        return "rarely_wasted"
    if rate <= 0.70:
        return "often_left_over"
    return "typical"


def _freshness(status: str, age_hours: float | None) -> dict:
    stale = status == "stale-upstream-error"
    if not stale:
        message = None
    elif age_hours is not None:
        message = (
            "We couldn't reach the dining data service just now, so this menu is "
            f"a stored copy from about {age_hours:.0f} hours ago. It may not "
            "reflect last-minute changes."
        )
    else:
        message = (
            "We couldn't reach the dining data service just now, so this menu is "
            "a stored copy and may not reflect last-minute changes."
        )
    return {"status": status, "age_hours": age_hours, "warn": stale, "message": message}


def _season_notice(location: int, date: str, serves_today: bool, serves_this_week: bool) -> str | None:
    if serves_today or serves_this_week:
        return None
    if location == ANNENBERG_LOCATION:
        return "no_menus"
    try:
        rows, _status = _svc().dining.day_rows(date, ANNENBERG_LOCATION)
    except Exception:
        return None
    return "hall_closed" if any("meal" in r for r in rows) else "no_menus"


def _declaration_window(
    location_id: int, served_on: str, meal: int
) -> tuple[bool, str | None, str | None]:
    if not takes_attendance(location_id):
        return False, None, None
    try:
        on = dt.date.fromisoformat(served_on)
    except ValueError:
        return False, None, None

    now = dt.datetime.now(LOCAL_TZ)
    closes = declaration_closes_at(location_id, on, meal, LOCAL_TZ)
    status = service_status(location_id, on, meal, LOCAL_TZ, now)
    return status == "upcoming", closes.isoformat(timespec="minutes"), status


REAL_TYPICAL_MIN_REPORTS = 6
REAL_TYPICAL_MIN_DAYS = 2

_BAND_HEIGHT = {"no_wait": 2 / 12, "short": 6 / 12, "long": 11 / 12}


def _real_typical(svc, location_id: int, on: dt.date) -> list[dict]:
    bins: dict[str, list[float]] = {}
    dates: set[dt.date] = set()
    for r in svc.store.line_report_history(location_id):
        at = dt.datetime.fromisoformat(r["created_at"]).replace(
            tzinfo=dt.timezone.utc).astimezone(LOCAL_TZ)
        if at.weekday() != on.weekday():
            continue
        dates.add(at.date())
        key = f"{at.hour:02d}:{30 * (at.minute // 30):02d}"
        bins.setdefault(key, []).append(_BAND_HEIGHT.get(r["band"], 0.0))
    n = sum(len(v) for v in bins.values())
    if n < REAL_TYPICAL_MIN_REPORTS or len(dates) < REAL_TYPICAL_MIN_DAYS:
        return []
    return [{"time": t, "busyness": sum(v) / len(v)}
            for t, v in sorted(bins.items())]


def _service_end_iso(location_id: int, served_on: str, meal: int) -> str | None:
    try:
        on = dt.date.fromisoformat(served_on)
    except ValueError:
        return None
    end = service_ends_at(location_id, on, meal, LOCAL_TZ)
    return None if end is None else end.isoformat(timespec="minutes")


def _demo_sub(account) -> str:
    if not DEMO_MODE:
        return account.sub
    return f"{account.sub}.{secrets.token_urlsafe(9)}"


def _award(user: User, kind: str, location_id: int | None,
           served_on: str | None, meal: int) -> dict | None:
    if location_id is None:
        return None

    svc = _svc()
    day = served_on or _today()
    today = _today()
    cents = GRANTS.get(kind, 0)

    before = svc.store.reward_summary(user.sub, today)
    if before.day_cents + cents > DAILY_CAP_CENTS:
        return {
            "simulated": True, "granted_cents": 0, "granted_display": None,
            "reason": LABELS.get(kind, kind), "already_earned": False,
            "capped": True,
            "pending_cents": before.pending_cents,
            "pending_display": format_cents(before.pending_cents),
        }

    granted = svc.store.grant_reward(
        user_id=user.sub, location_id=location_id, served_on=day,
        meal=meal, kind=kind, cents=cents, granted_on=today,
    )
    after = svc.store.reward_summary(user.sub, today)
    return {
        "simulated": True,
        "granted_cents": granted,
        "granted_display": format_cents(granted) if granted else None,
        "reason": LABELS.get(kind, kind),
        "already_earned": granted == 0,
        "capped": False,
        "pending_cents": after.pending_cents,
        "pending_display": format_cents(after.pending_cents),
    }


def _item(recipe: dict, rating, consumption, availability=None) -> dict:
    spice = spice_for(recipe)
    return {
        "id": recipe["id"],
        "name": recipe.get("name"),
        "calories": recipe.get("calories"),
        "serving_size": recipe.get("serving_size"),
        "vegan": bool(recipe.get("vegan")),
        "vegetarian": bool(recipe.get("vegetarian")),
        "allergens": recipe.get("allergens") or [],
        "spice": {"level": spice.level, "curated": spice.curated, "basis": spice.basis},
        "rating": None if rating is None else {
            "average": rating.average, "count": rating.count,
            "recent_average": rating.recent_average,
            "recent_count": rating.recent_count,
            "recent_days": RATING_RECENT_DAYS,
        },
        "consumption": None if consumption is None else {
            "rate": consumption.rate,
            "observations": consumption.observations,
            "band": _band(consumption.rate),
        },
        "availability": availability,
    }


def staff_required(view):

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            abort(401, "Sign in to use kitchen tools.")
        if user.affiliation != "staff":
            abort(403, "Kitchen tools are staff-only.")
        if user.demo and not kitchen_unlocked():
            abort(403, "Kitchen actions are locked on this browser. "
                       "Unlock with the kitchen passcode.")
        return view(*args, **kwargs)

    return wrapper


_unlock_gate = {"t": -1e9}


def _grill_serving_meal(location: int) -> int | None:
    now = dt.datetime.now(LOCAL_TZ)
    for meal in (0, 1, 2):
        if service_status(location, now.date(), meal, LOCAL_TZ, now) == "serving":
            return meal
    return 2 if DEMO_MODE else None


_ORDER_MSG = {
    "placed": "Order placed — not yet seen by the grill.",
    "seen": "The grill has your order.",
    "cooking": "Your meal is being prepared — head to the grill to get it fresh.",
    "ready": "Your meal is ready for pickup.",
    "collected": "Collected — enjoy.",
    "cancelled": "This order was cancelled.",
}

MSG_ORDER_IN_PROGRESS = "You already have a grill order in progress."

MSG_GRILL_CLOSED = ("The grill had to close and your order was cancelled — "
                    "come to the dining hall to order in person.")

MSG_GRILL_NOT_PERMITTED = ("Interhouse rules don't allow you to dine here "
                           "for this meal, so the grill can't take your order.")

_DINE_OK = frozenset({"open", "unknown"})


def _may_dine(user: User, location: int, meal: int) -> tuple[bool, str | None]:
    if user.affiliation == "staff" or not user.house_key:
        return True, None
    on = dt.datetime.now(LOCAL_TZ).date()
    for v in where_can_i_eat(meal, on, user.house_key):
        if v.location_id == location:
            if v.access.value in _DINE_OK:
                return True, None
            return False, v.reason
    return True, None

_GRILL_REFUSALS = {
    "not_serving": (400, "This hall is not serving right now, so the "
                         "grill is not taking orders."),
    "closed": (409, "The grill is closed to app orders right now."),
    "paused": (409, "The grill has paused online orders — you can order "
                    "in person at the counter."),
    "station_offline": (409, "The grill isn't taking online orders right now."),
    "walk_up": (409, "The grill is taking orders in person right now — "
                     "come down and order at the counter."),
    "wait_cap": (409, "The grill is backed up with online orders — "
                      "come down and order at the counter."),
}


def _pickup_name(full: str) -> str:
    parts = (full or "").split()
    if not parts:
        return "Student"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def _order_view(o: dict) -> dict:
    return {
        "id": o["id"], "status": o["status"],
        "location_id": o["location_id"],
        "main": {"id": o["main_id"], "name": o["main_name"]},
        "condiments": json.loads(o["condiments"]),
        "message": _ORDER_MSG.get(o["status"]),
        "cancellable": o["status"] in ("placed", "seen"),
        "placed_at": o["placed_at"],
        "cancel_reason": o["cancel_reason"],
    }


def report_key_required(view):

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        token = reports.bearer_token(request.headers.get("Authorization"))
        if token is None:
            abort(401, "Send a report key: Authorization: Bearer <key>.")
        record = _svc().store.verify_report_key(reports.hash_key(token))
        if record is None:
            abort(401, "Unknown or revoked report key.")
        if not reports.has_scope(record["scopes"], "reports:read"):
            abort(403, "This key does not carry the reports:read scope.")
        g.report_key = record
        return view(*args, **kwargs)

    return wrapper


def create_app() -> APIFlask:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("veritaste")

    app = APIFlask(
        __name__,
        title="Veritaste API",
        version=__version__,
        docs_path="/api/docs",
        spec_path="/api/openapi.json",
        static_folder=str(WEB_DIR),
        static_url_path="",
    )
    app.description = DISCLAIMER
    app.config["DESCRIPTION"] = DISCLAIMER
    app.config["SPEC_FORMAT"] = "json"

    store = build_store()
    store.init_schema()
    app.extensions["veritaste"] = type("Svc", (), {
        "store": store,
        "dining": DiningSource(store),
        "lines": SimulatedLineProvider(),
    })()
    log.info("store=%s db=%s cache_ttl=%sh", STORE_BACKEND, DB_PATH, CACHE_TTL_HOURS)

    @app.error_processor
    def envelope(e):
        return (
            {"status": "FAIL", "error": e.message, "detail": e.detail or None},
            e.status_code,
            e.headers,
        )

    @app.before_request
    def gateway_secret():
        if not ECS_APIKEY:
            return None
        if request.path in ("/monitor/health", "/health"):
            return None
        if not request.path.startswith(API):
            return None
        if request.headers.get("x-api-key") != ECS_APIKEY:
            abort(401, "Missing or invalid gateway credential.")
        return None

    _register(app)
    return app


def _register(app: APIFlask) -> None:


    @app.get("/monitor/health")
    @app.doc(tags=["platform"], summary="Liveness check (ECS/Apigee convention)")
    def monitor_health():
        return {"status": "PASS", "version": __version__}

    @app.get("/health")
    @app.doc(tags=["platform"], summary="Liveness check (EKS ingress convention)")
    def health():
        return {"status": "PASS", "version": __version__}

    @app.get(f"{API}/meta")
    @app.doc(tags=["platform"], summary="Build and configuration summary")
    def meta():
        return {
            "status": "PASS",
            "version": __version__,
            "store": STORE_BACKEND,
            "cache_ttl_hours": CACHE_TTL_HOURS,
            "mode": MODE,
            "notifications": push.enabled(),
            "disclaimer": DISCLAIMER,
        }


    @app.get(f"{API}/locations")
    @app.doc(tags=["dining"], summary="HUDS dining locations")
    def locations():
        return jsonify(_svc().dining.locations())

    @app.get(f"{API}/menu")
    @app.input(MenuQuery, location="query")
    @app.doc(tags=["dining"], summary="Menu for a hall, date and meal, with Veritaste signals")
    def menu(query_data):
        date, location, meal = query_data["date"], query_data["location"], query_data["meal"]
        svc = _svc()
        day, status = svc.dining.day_rows(date, location)
        rows = [r for r in day if r.get("meal") == meal]
        freshness = _freshness(status, svc.dining.cache_age_hours(date, location))

        brunch = is_brunch_day(day)
        served = sorted({r["meal"] for r in day if "meal" in r})
        profile = svc.dining.service_profile(date, location)
        window = _declaration_window(location, date, meal)

        base = {
            "date": date, "location": location, "meal": meal,
            "meal_name": meal_name(location, meal, is_brunch=brunch),
            "freshness": freshness,
            "takes_attendance": takes_attendance(location),
            "declaration_open": window[0],
            "declaration_closes_at": window[1],
            "service_status": window[2],
            "service_ends_at": _service_end_iso(location, date, meal),
            "season_notice": _season_notice(
                location, date, bool(served), bool(profile)
            ),
            "meals_served": served,
            "meal_options": [
                {
                    "meal": m,
                    "name": meal_name(location, m, is_brunch=brunch),
                    "served": m in served,
                }
                for m in profile
            ],
        }
        if not rows:
            return {**base, "categories": [], "item_count": 0}

        recipe_ids = sorted({r["recipe"] for r in rows})
        recipes = svc.dining.recipes(recipe_ids)
        categories = {c["id"]: c["name"] for c in svc.dining.categories()}
        ratings = svc.store.rating_summary(recipe_ids, location, RATING_RECENT_DAYS)
        consumption = svc.store.consumption_signals(recipe_ids)
        stock = {
            m["recipe_id"]: {"status": m["status"], "note": m["note"]}
            for m in svc.store.stock_marks(location, date)
        }

        grouped: dict[str, list[dict]] = {}
        seen: set[tuple[str, int]] = set()
        for row in rows:
            recipe = recipes.get(row["recipe"])
            if recipe is None:
                continue
            cat = categories.get(row["category"], "Other")
            if (cat, recipe["id"]) in seen:
                continue
            seen.add((cat, recipe["id"]))
            grouped.setdefault(cat, []).append(
                _item(recipe, ratings.get(recipe["id"]), consumption.get(recipe["id"]),
                      stock.get(recipe["id"]))
            )

        return {
            **base,
            "item_count": len(seen),
            "categories": [{"name": n, "items": i} for n, i in grouped.items()],
        }

    @app.get(f"{API}/recipes/<int:recipe_id>")
    @app.input(RecipeQuery, location="query")
    @app.doc(tags=["dining"], summary="One dish — the QR/NFC scan target, no login")
    def recipe_detail(recipe_id: int, query_data):
        svc = _svc()
        try:
            recipe = svc.dining.recipe(recipe_id)
        except Exception:
            abort(404, f"No recipe {recipe_id}")

        location = query_data.get("location")
        spice = spice_for(recipe)
        rating = svc.store.rating_summary(
            [recipe_id], location, RATING_RECENT_DAYS
        ).get(recipe_id)
        consumption = svc.store.consumption_signals([recipe_id]).get(recipe_id)

        user = current_user()
        yours = (svc.store.user_rating(user.sub, recipe_id, location)
                 if user is not None and location is not None else None)

        stock = None
        if location is not None:
            for m in svc.store.stock_marks(location, _today()):
                if m["recipe_id"] == recipe_id:
                    stock = {"status": m["status"], "note": m["note"]}
                    break

        your_note = None
        if (user is not None and location is not None
                and query_data.get("meal") is not None):
            your_note = svc.store.feedback_of(
                user.sub, recipe_id, location, _today(), query_data["meal"])

        return {
            **recipe,
            "availability": stock,
            "spice": {"level": spice.level, "curated": spice.curated, "basis": spice.basis},
            "your_rating": yours,
            "your_feedback": your_note,
            "rating": None if rating is None else {
                "average": rating.average, "count": rating.count,
                "recent_average": rating.recent_average,
                "recent_count": rating.recent_count,
                "recent_days": RATING_RECENT_DAYS,
            },
            "consumption": None if consumption is None else {
                "rate": consumption.rate,
                "observations": consumption.observations,
                "band": _band(consumption.rate),
            },
        }

    @app.get(f"{API}/line/<int:location_id>")
    @app.doc(tags=["signals"], summary="Current servery busyness (simulated)")
    def line_now(location_id: int):
        svc = _svc()
        latest = svc.store.latest_line_report(location_id)
        if latest is not None:
            fresh = (latest["band"] is not None
                     and latest["expires_at"] > dt.datetime.utcnow().isoformat())
            return {
                "location": location_id,
                "source": "staff",
                "band": latest["band"] if fresh else None,
                "reported_at": latest["created_at"] if fresh else None,
                "expires_at": latest["expires_at"] if fresh else None,
                "basis": "Reported by the kitchen; a report expires half an "
                         "hour after it is made.",
            }
        r = svc.lines.current(location_id)
        return {
            "location": r.location_id,
            "source": "simulated",
            "at": r.at.isoformat(timespec="minutes"),
            "wait_minutes": r.wait_minutes,
            "busyness": r.busyness,
            "entries_per_min": r.entries_per_min,
            "simulated": r.simulated,
            "basis": "Simulated from published HUDS swipe-rate curves. Replace "
                     "with a real entry-swipe feed via SwipeFeedLineProvider.",
        }

    @app.get(f"{API}/line/<int:location_id>/typical")
    @app.input(LineQuery, location="query")
    @app.doc(tags=["signals"], summary="Historical busyness by time of day")
    def line_typical(location_id: int, query_data):
        on = _parse_date(query_data["date"])
        svc = _svc()
        if svc.store.latest_line_report(location_id) is not None:
            return {
                "location": location_id, "date": on.isoformat(),
                "weekday": on.strftime("%A"),
                "series": _real_typical(svc, location_id, on),
                "simulated": False,
            }
        series = svc.lines.typical_day(location_id, on)
        return {
            "location": location_id, "date": on.isoformat(),
            "weekday": on.strftime("%A"),
            "series": [{"time": t.strftime("%H:%M"), "busyness": v} for t, v in series],
            "simulated": True,
        }

    @app.post(f"{API}/line/<int:location_id>/report")
    @staff_required
    @app.input(LineReportIn)
    @app.doc(tags=["signals"],
             summary="Report the line as a band — expires in 30 minutes (staff)")
    def line_report(location_id: int, json_data):
        import app.main as _m
        expires = (dt.datetime.utcnow()
                   + dt.timedelta(minutes=_m.LINE_REPORT_TTL_MIN)).isoformat()
        _svc().store.add_line_report(
            location_id, json_data["band"], current_user().sub, expires)
        return {"recorded": True, "band": json_data["band"],
                "expires_at": expires}


    @app.get(f"{API}/forecast")
    @staff_required
    @app.input(ForecastQuery, location="query")
    @app.doc(tags=["forecast"],
             summary="Production-planning view for one service (staff)")
    def forecast_view(query_data):
        svc = _svc()
        location = query_data["location"]
        meal = query_data["meal"]
        served_on = query_data.get("date") or _today()
        try:
            on = dt.date.fromisoformat(served_on)
        except ValueError:
            abort(422, "Dates look like 2026-08-02.")

        yes, no = svc.store.attendance_counts(location, served_on, meal)
        sql_dow = str((on.weekday() + 1) % 7)
        base_avg, base_days = svc.store.attendance_baseline(
            location, sql_dow, meal, served_on)

        weekday = on.strftime("%A")
        variables = [
            {"id": "declared", "label": "Declared intent",
             "value": f"{yes} coming · {no} not coming",
             "provenance": "live", "impact": None},
            {"id": "history", "label": f"Prior {weekday}s",
             "value": (f"avg {base_avg:.0f} declared over {base_days} "
                       f"day{'s' if base_days != 1 else ''}"
                       if base_days else None),
             "provenance": "live" if base_days else "absent", "impact": None},
            {"id": "weather", "label": "Weather",
             "value": None, "provenance": "absent", "impact": None},
            {"id": "calendar", "label": "Calendar effects",
             "value": None, "provenance": "absent", "impact": None},
            {"id": "swipes", "label": "Swipe history",
             "value": None, "provenance": "absent", "impact": None},
        ]

        extremes = svc.store.rated_extremes(location, 3)
        week_ago = (on - dt.timedelta(days=7)).isoformat()
        wasted = svc.store.top_wasted(location, week_ago, 3)

        day, _status = svc.dining.day_rows(served_on, location)
        today_ids = {r["recipe"] for r in day if r.get("meal") == meal}
        seen_before: set[int] = set()
        for back in range(1, 7):
            prior, _s = svc.dining.day_rows(
                (on - dt.timedelta(days=back)).isoformat(), location)
            seen_before |= {r["recipe"] for r in prior}
        new_ids = sorted(today_ids - seen_before)[:6]

        ids = sorted({*(r["recipe_id"] for r in extremes["top"]),
                      *(r["recipe_id"] for r in extremes["bottom"]),
                      *(r["recipe_id"] for r in wasted), *new_ids})[:150]
        names = {rid: (rec or {}).get("name")
                 for rid, rec in svc.dining.recipes(ids).items()}
        for row in (*extremes["top"], *extremes["bottom"], *wasted):
            row["name"] = names.get(row["recipe_id"])
        new_ratings = svc.store.rating_summary(new_ids, location,
                                               RATING_RECENT_DAYS)
        new_items = [{
            "recipe_id": rid, "name": names.get(rid),
            "average": getattr(new_ratings.get(rid), "average", None),
            "count": getattr(new_ratings.get(rid), "count", 0),
        } for rid in new_ids]

        return {
            "location": location, "date": served_on, "meal": meal,
            "declared": {"coming": yes, "not_coming": no,
                         "provenance": "live"},
            "baseline": {"average": base_avg, "days": base_days,
                         "weekday": weekday,
                         "provenance": "live" if base_days else "absent"},
            "model": {"provenance": "absent"},
            "variables": variables,
            "rated": extremes,
            "wasted": [{**w, "provenance": "simulated"} for w in wasted],
            "new_items": new_items,
        }

    @app.get(f"{API}/houses")
    @app.doc(tags=["reference"], summary="Houses and the servery each uses")
    def houses():
        return {
            "houses": [
                {"key": h.key, "name": h.name,
                 "neighborhood": h.neighborhood.value,
                 "location_id": h.location_id, "location_name": h.location_name}
                for h in HOUSES
            ],
            "open_locations": open_dining_locations(),
        }

    @app.get(f"{API}/interhouse")
    @app.input(InterhouseQuery, location="query")
    @app.doc(tags=["reference"], summary="Where this student may eat, by meal and date")
    def interhouse(query_data):
        on = _parse_date(query_data["date"])
        meal = query_data["meal"]
        user = current_user()
        viewer = query_data.get("house") or (user.house_key if user else None)
        if viewer is not None and viewer not in BY_KEY:
            abort(400, f"Unknown house {viewer!r}")

        return {
            "date": on.isoformat(), "weekday": on.strftime("%A"),
            "meal": meal, "meal_name": MEAL_NAMES.get(meal, str(meal)),
            "viewer_house": viewer,
            "viewer_house_name": BY_KEY[viewer].name if viewer in BY_KEY else None,
            "halls": [
                {"house": v.house_key, "house_name": v.house_name,
                 "location_id": v.location_id, "location_name": v.location_name,
                 "access": v.access.value, "reason": v.reason,
                 "source": v.source, "confidence": v.confidence.value,
                 "is_home": v.is_home}
                for v in where_can_i_eat(meal, on, viewer)
            ],
            "open_locations": open_dining_locations(),
            "caveat": "Interhouse rules are set per House and change during the "
                      "year. Confirm with the House before relying on this.",
        }

    @app.get(f"{API}/attendance")
    @app.input(AttendanceQuery, location="query")
    @app.doc(tags=["signals"], summary="Declared attendance counts for a service")
    def attendance(query_data):
        yes, no = _svc().store.attendance_counts(
            query_data["location"], query_data["date"], query_data["meal"]
        )
        return {
            "location": query_data["location"], "date": query_data["date"],
            "meal": query_data["meal"],
            "declared_attending": yes, "declared_absent": no,
        }


    @app.post(f"{API}/ratings")
    @app.input(RatingIn)
    @login_required
    @app.doc(tags=["feedback"], summary="Rate a dish (requires sign-in)")
    def add_rating(json_data):
        user = current_user()
        svc = _svc()
        location_id = json_data["location_id"]
        changed = svc.store.add_rating(
            recipe_id=json_data["recipe_id"], score=json_data["score"],
            user_id=user.sub, location_id=location_id,
            served_on=json_data.get("served_on"),
            recent_days=RATING_RECENT_DAYS,
        )
        got = svc.store.rating_summary(
            [json_data["recipe_id"]], location_id, RATING_RECENT_DAYS
        ).get(json_data["recipe_id"])
        reward = _award(
            user, "rating", json_data.get("location_id"),
            json_data.get("served_on"), DAY_SCOPED,
        )
        return {
            "recorded": True, "recipe_id": json_data["recipe_id"],
            "changed": changed,
            "average": got.average if got else float(json_data["score"]),
            "count": got.count if got else 1,
            "recent_average": got.recent_average if got else float(json_data["score"]),
            "recent_count": got.recent_count if got else 1,
            "reward": reward,
        }, 201

    @app.post(f"{API}/attendance")
    @app.input(AttendanceIn)
    @login_required
    @app.doc(tags=["feedback"],
             summary="Declare whether you are coming (requires sign-in)")
    def set_attendance(json_data):
        user = current_user()
        svc = _svc()
        location_id = json_data["location_id"]

        if not takes_attendance(location_id):
            abort(400, f"{RETAIL_LOCATIONS[location_id]} serves walk-up customers, "
                       "so there is nothing to tell the kitchen in advance.")

        served_on = json_data.get("served_on") or _today()

        open_now, _closes_at, status = _declaration_window(
            location_id, served_on, json_data["meal"]
        )
        if not open_now:
            abort(400, "Declarations for that meal have closed — the kitchen has "
                       "already cooked to it."
                       if status in ("serving", "over") else
                       "That meal cannot be declared for.")
        svc.store.set_attendance_intent(
            user_id=user.sub, location_id=location_id,
            served_on=served_on, meal=json_data["meal"],
            attending=json_data["attending"],
        )
        yes, no = svc.store.attendance_counts(
            location_id, served_on, json_data["meal"]
        )
        reward = _award(
            user, "attendance", location_id, served_on, json_data["meal"]
        )
        return {
            "recorded": True, "attending": json_data["attending"],
            "declared_attending": yes, "declared_absent": no,
            "reward": reward,
        }, 201


    @app.get(f"{API}/me")
    @app.doc(tags=["auth"], summary="Current session, if any")
    def me():
        user = current_user()
        if user is None:
            return {"signed_in": False}
        return {
            "signed_in": True, "name": user.name,
            "principal": user.principal, "affiliation": user.affiliation,
            "house_key": user.house_key,
            "house_name": BY_KEY[user.house_key].name
                          if user.house_key in BY_KEY else None,
            "demo": user.demo,
            "kitchen": kitchen_unlocked(),
            "feedback_paused": _svc().store.feedback_blocked(user.sub),
        }

    @app.post(f"{API}/auth/kitchen")
    @app.input(KitchenIn)
    @app.doc(tags=["auth"],
             summary="Unlock kitchen actions on this browser for 24 hours")
    def kitchen_unlock(json_data):
        if not STAFF_PASSCODE:
            abort(503, "Staff access is not configured on this server.")
        now = time.monotonic()
        wait = UNLOCK_WINDOW_S - (now - _unlock_gate["t"])
        if wait > 0:
            abort(429, f"One attempt every {UNLOCK_WINDOW_S} seconds — "
                       f"try again in {int(wait) + 1}s.")
        _unlock_gate["t"] = now
        supplied = hashlib.sha256(json_data["passcode"].encode()).digest()
        expected = hashlib.sha256(STAFF_PASSCODE.encode()).digest()
        if not hmac.compare_digest(supplied, expected):
            time.sleep(0.3)
            abort(401, "That is not the kitchen passcode.")
        resp = jsonify({"unlocked": True, "hours": KITCHEN_TTL_S // 3600})
        resp.set_cookie(
            KITCHEN_COOKIE, issue_kitchen(), max_age=KITCHEN_TTL_S,
            httponly=True, samesite="Lax", secure=request.is_secure, path="/",
        )
        return resp

    @app.delete(f"{API}/auth/kitchen")
    @app.doc(tags=["auth"], summary="Lock kitchen actions on this browser")
    def kitchen_lock():
        resp = jsonify({"unlocked": False})
        resp.delete_cookie(KITCHEN_COOKIE, path="/")
        return resp

    @app.get(f"{API}/auth/accounts")
    @app.doc(tags=["auth"], summary="Selectable demonstration identities")
    def auth_accounts():
        return {
            "accounts": public_list(),
            "notice": "Demonstration accounts only. This is not a Harvard login "
                      "and no real credential is ever accepted.",
        }

    @app.post(f"{API}/auth/demo-signin")
    @app.input(SignInIn)
    @app.doc(tags=["auth"], summary="Complete the mock authorization-code exchange")
    def demo_signin(json_data):
        account = BY_NETID[json_data["netid"]]
        user = User(
            sub=_demo_sub(account), name=account.name,
            principal=account.principal, affiliation=account.affiliation,
            house_key=account.house_key if account.house_key in BY_KEY else None,
            demo=True,
        )
        resp = jsonify({
            "signed_in": True, "name": user.name, "netid": account.netid,
            "affiliation": user.affiliation, "house_key": user.house_key,
            "demo": True, "mode": MODE,
        })
        resp.set_cookie(SESSION_COOKIE, issue_session(user),
                        httponly=True, samesite="Lax", max_age=12 * 3600)
        return resp

    @app.post(f"{API}/auth/signout")
    @app.doc(tags=["auth"], summary="Clear the session")
    def signout():
        user = current_user()
        if user is not None and DEMO_MODE:
            store = _svc().store
            for sub in store.push_subs(user_id=user.sub):
                store.delete_push_sub(sub.endpoint)

        resp = jsonify({"signed_in": False})
        resp.delete_cookie(SESSION_COOKIE)
        return resp


    @app.get(f"{API}/rewards/me")
    @login_required
    @app.doc(tags=["rewards"], summary="Simulated BoardPlus ledger for this student")
    def rewards_me():
        user = current_user()
        today = _today()
        summary = _svc().store.reward_summary(user.sub, today)
        return {
            "simulated": True,
            "pending_cents": summary.pending_cents,
            "pending_display": format_cents(summary.pending_cents),
            "today": today,
            "timezone": TIMEZONE,
            "cap_cents": DAILY_CAP_CENTS,
            "cap_display": format_cents(DAILY_CAP_CENTS),
            "day_cents": summary.day_cents,
            "grant_count": summary.grant_count,
            "grants": [
                {
                    "kind": g.kind, "reason": LABELS.get(g.kind, g.kind),
                    "cents": g.cents, "display": format_cents(g.cents),
                    "location_id": g.location_id, "served_on": g.served_on,
                    "granted_on": g.granted_on,
                    "meal": None if g.meal == DAY_SCOPED else g.meal,
                }
                for g in summary.recent
            ],
            "earn_rates": {
                kind: {"cents": cents, "display": format_cents(cents),
                       "reason": LABELS.get(kind, kind)}
                for kind, cents in GRANTS.items()
            },
            "settlement": settlement_note(),
        }


    @app.get(f"{API}/push/vapid")
    @app.doc(tags=["notifications"], summary="Application server key for subscribing")
    def push_vapid():
        return {"enabled": push.enabled(), "public_key": push.public_key()}

    @app.post(f"{API}/push/subscriptions")
    @app.input(PushSubIn)
    @login_required
    @app.doc(tags=["notifications"], summary="Register this browser for notifications")
    def push_subscribe(json_data):
        if not push.enabled():
            abort(503, "Notifications are not configured on this server.")
        user = current_user()
        _svc().store.put_push_sub(PushSub(
            endpoint=json_data["endpoint"], user_sub=user.sub,
            affiliation=user.affiliation, house_key=user.house_key,
            p256dh=json_data["p256dh"], auth=json_data["auth"],
        ))
        return {"subscribed": True}, 201

    @app.delete(f"{API}/push/subscriptions")
    @app.input(PushEndpointIn, location="query")
    @login_required
    @app.doc(tags=["notifications"], summary="Unsubscribe this browser")
    def push_unsubscribe(query_data):
        _svc().store.delete_push_sub(query_data["endpoint"])
        return {"subscribed": False}

    @app.post(f"{API}/push/test")
    @login_required
    @app.doc(tags=["notifications"],
             summary="Send this account's own subscriptions a preview notification")
    def push_test():
        if not push.enabled():
            abort(503, "Notifications are not configured on this server.")

        user = current_user()
        svc = _svc()
        subs = svc.store.push_subs(user_id=user.sub)
        if not subs:
            abort(409, "This browser is not subscribed to notifications yet.")

        if user.affiliation == "staff":
            body = ("Preview — before prep: yesterday's most-wasted items are "
                    "waiting for a production decision.")
            target = "/"
        else:
            body = ("Preview — one tap tells the kitchen your plans for tonight. "
                    "Saying you're not coming helps them most.")
            target = "/"

        sent, reasons = 0, []
        for sub in subs:
            ok, status, reason = push.send(
                sub.as_subscription_info(), "Veritaste", body, target
            )
            if ok:
                sent += 1
            else:
                if reason:
                    reasons.append(reason)
                if status in push.DEAD_STATUSES:
                    svc.store.delete_push_sub(sub.endpoint)
        return {"sent": sent, "subscriptions": len(subs),
                "reason": reasons[0] if reasons else None}


    @app.get(f"{API}/availability")
    @app.input(AvailabilityQuery, location="query")
    @app.doc(tags=["availability"], summary="Today's low/out marks for a hall")
    def availability(query_data):
        svc = _svc()
        location = query_data["location"]
        marks = svc.store.stock_marks(location, _today())
        names = {}
        if marks:
            fetched = svc.dining.recipes([m["recipe_id"] for m in marks])
            names = {rid: rec.get("name") for rid, rec in fetched.items()}
        for m in marks:
            m["name"] = names.get(m["recipe_id"])
        marks.sort(key=lambda m: ((m["name"] or "").lower() or "￿",
                                  m["recipe_id"]))
        return {"location": location, "date": _today(), "marks": marks}

    @app.post(f"{API}/availability")
    @staff_required
    @app.input(StockIn)
    @app.doc(tags=["availability"],
             summary="Mark a dish low or out at a hall (staff)")
    def set_availability(json_data):
        svc = _svc()
        location, rid = json_data["location_id"], json_data["recipe_id"]
        svc.store.set_stock(
            location, rid, json_data["status"], json_data.get("note"),
            current_user().sub, _today(),
        )
        if json_data["status"] == "out":
            affected = svc.store.grill_orders_containing(location, rid)
            if affected:
                rec = svc.dining.recipes([rid]).get(rid) or {}
                name = rec.get("name") or "An item in your grill order"
                for o in affected:
                    if o["status"] in ("placed", "seen"):
                        _notify_user(svc, o["user_id"],
                                     f"{name} ran out — you can cancel your "
                                     "grill order if you'd rather.")
                    else:
                        _notify_user(svc, o["user_id"],
                                     f"{name} ran out — your order is "
                                     "already on the grill.")
        return {"ok": True, "status": json_data["status"],
                "recipe_id": json_data["recipe_id"]}

    @app.delete(f"{API}/availability")
    @staff_required
    @app.input(StockClearQuery, location="query")
    @app.doc(tags=["availability"],
             summary="Clear a mark — the dish is back (staff)")
    def clear_availability(query_data):
        cleared = _svc().store.clear_stock(
            query_data["location"], query_data["recipe"], current_user().sub,
        )
        return {"cleared": cleared}


    def _feedback_display_name(user) -> str:
        parts = (user.name or "").split()
        if not parts:
            return "A student"
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0]} {parts[-1][0]}."

    @app.post(f"{API}/feedback")
    @app.input(FeedbackIn)
    @login_required
    @app.doc(tags=["feedback"],
             summary="Tell the kitchen about a dish (requires sign-in)")
    def add_feedback(json_data):
        user = current_user()
        svc = _svc()
        if svc.store.feedback_blocked(user.sub):
            abort(403, "Your feedback is valued, but sending from this "
                       "account is paused right now. The pause can be "
                       "temporary — ask at the servery if you have questions.")
        text = json_data["text"].strip()
        if not text:
            abort(400, "The note is empty.")
        location = json_data["location_id"]
        meal = json_data["meal"]
        recipe = json_data["recipe_id"]
        today = _today()
        day, _status = svc.dining.day_rows(today, location)
        if not any(r.get("recipe") == recipe and r.get("meal") == meal
                   for r in day):
            abort(422, "That dish is not on today's menu at this hall.")
        signed = bool(json_data["signed"])
        changed = svc.store.upsert_feedback(
            user_id=user.sub, recipe_id=recipe, location_id=location,
            served_on=today, meal=meal, text=text, signed=signed,
            signed_name=_feedback_display_name(user) if signed else None,
            source=json_data["source"],
        )
        return {"recorded": True, "changed": changed}

    @app.get(f"{API}/feedback")
    @staff_required
    @app.input(FeedbackQuery, location="query")
    @app.doc(tags=["feedback"],
             summary="Notes students sent the kitchen (staff)")
    def feedback_inbox(query_data):
        svc = _svc()
        rows = svc.store.feedback_for_location(
            query_data["location"], query_data.get("date") or _today())
        ids = sorted({r["recipe_id"] for r in rows})[:150]
        fetched = svc.dining.recipes(ids)
        names = {rid: (rec or {}).get("name") for rid, rec in fetched.items()}
        for r in rows:
            r["name"] = names.get(r["recipe_id"])
        return {"location": query_data["location"], "notes": rows}

    @app.post(f"{API}/feedback/blocks")
    @staff_required
    @app.input(FeedbackBlockIn)
    @app.doc(tags=["feedback"],
             summary="Pause a note's author from feedback (staff, name-blind)")
    def block_feedback_author(json_data):
        svc = _svc()
        author = svc.store.feedback_author(json_data["note_id"])
        if author is None:
            abort(404, "No such note.")
        svc.store.set_feedback_block(
            json_data["note_id"], author, current_user().sub,
            json_data.get("reason"))
        return {"blocked": True}

    @app.delete(f"{API}/feedback/blocks")
    @staff_required
    @app.input(FeedbackBlockQuery, location="query")
    @app.doc(tags=["feedback"],
             summary="Reinstate a note's author (staff)")
    def unblock_feedback_author(query_data):
        svc = _svc()
        author = svc.store.feedback_author(query_data["note_id"])
        if author is None:
            abort(404, "No such note.")
        return {"unblocked": svc.store.clear_feedback_block(
            query_data["note_id"], current_user().sub)}


    def _notify_user(svc, user_id: str, body: str, url: str = "/") -> None:
        for sub in svc.store.push_subs(user_id=user_id):
            ok, status, _reason = push.send(
                sub.as_subscription_info(), "Veritaste", body, url)
            if status in push.DEAD_STATUSES:
                svc.store.delete_push_sub(sub.endpoint)

    def _grill_items(svc, location: int, meal: int) -> list[dict]:
        date = _today()
        day, _status = svc.dining.day_rows(date, location)
        categories = {c["id"]: c["name"] for c in svc.dining.categories()}
        grill_rows = [
            r for r in day
            if r.get("meal") == meal
            and (categories.get(r.get("category"), "").strip().lower()
                 == GRILL_CATEGORY)
        ]
        ids = sorted({r["recipe"] for r in grill_rows})
        recipes = svc.dining.recipes(ids)
        ratings = svc.store.rating_summary(ids, location, RATING_RECENT_DAYS)
        consumption = svc.store.consumption_signals(ids)
        stock = {m["recipe_id"]: {"status": m["status"], "note": m["note"]}
                 for m in svc.store.stock_marks(location, date)}
        items = []
        for rid in ids:
            if rid not in recipes:
                continue
            item = _item(recipes[rid], ratings.get(rid), consumption.get(rid),
                         stock.get(rid))
            item["out"] = (stock.get(rid) or {}).get("status") == "out"
            items.append(item)
        return items

    def _close_grill(svc, location: int) -> dict:
        for o in svc.store.grill_open_orders(location):
            if svc.store.cancel_grill_order(o["id"], "closed", by_staff=True):
                _notify_user(svc, o["user_id"], MSG_GRILL_CLOSED)
        return svc.store.set_grill_station(
            location, GRILL_APP_CAP_DEFAULT, state="closed")

    def _shift_gate(svc, location: int, station: dict) -> dict:
        meal = _grill_serving_meal(location)
        now = dt.datetime.now(LOCAL_TZ)
        touched = dt.datetime.fromisoformat(
            station["updated_at"]).replace(tzinfo=dt.timezone.utc)

        if meal is not None:
            end = service_ends_at(location, now.date(), meal, LOCAL_TZ)
            if (end is not None and station["state"] == "accepting"):
                threshold = end - dt.timedelta(minutes=GRILL_LAST_CALL_MIN)
                if now >= threshold and touched < threshold:
                    return svc.store.set_grill_station(
                        location, GRILL_APP_CAP_DEFAULT, state="paused")
            return station

        if station["state"] == "closed":
            return station
        remaining = []
        for o in svc.store.grill_open_orders(location):
            if o["status"] in ("placed", "seen"):
                if svc.store.cancel_grill_order(o["id"], "closed", by_staff=True):
                    _notify_user(svc, o["user_id"], MSG_GRILL_CLOSED)
            else:
                remaining.append(o)
        if remaining:
            if station["state"] != "paused":
                station = svc.store.set_grill_station(
                    location, GRILL_APP_CAP_DEFAULT, state="paused")
            return station
        return svc.store.set_grill_station(
            location, GRILL_APP_CAP_DEFAULT, state="closed")

    def _grill_items_for_service(svc, location: int, meal: int | None) -> list[dict]:
        if meal is None:
            return []
        items = _grill_items(svc, location, meal)
        if DEMO_MODE and not items and meal != 2:
            items = _grill_items(svc, location, 2)
        return items

    def _grill_snapshot(svc, location: int):
        station = _shift_gate(
            svc, location, svc.store.grill_station(location, GRILL_APP_CAP_DEFAULT))
        open_count = svc.store.open_app_count(location)
        est_s = svc.store.grill_cook_estimate_s(location, GRILL_COOK_S_DEFAULT)
        wait_min = round(open_count * est_s / 60)

        online = False
        if station["heartbeat_at"]:
            age = (dt.datetime.utcnow()
                   - dt.datetime.fromisoformat(station["heartbeat_at"])
                   ).total_seconds()
            online = age <= GRILL_HEARTBEAT_STALE_S

        meal = _grill_serving_meal(location)
        reasons = []
        if meal is None:
            reasons.append("not_serving")
        if station["state"] == "closed":
            reasons.append("closed")
        if station["state"] == "paused":
            reasons.append("paused")
        if not online:
            reasons.append("station_offline")
        if open_count >= station["app_cap"]:
            reasons.append("walk_up")
        if wait_min > GRILL_WAIT_CAP_MIN:
            reasons.append("wait_cap")

        return station, meal, {
            "state": station["state"],
            "accepting_now": not reasons,
            "why_not": reasons or None,
            "station_online": online,
            "open_app_orders": open_count,
            "app_cap": station["app_cap"],
            "estimated_wait_min": wait_min,
            "wait_cap_min": GRILL_WAIT_CAP_MIN,
        }


    @app.get(f"{API}/grill")
    @app.input(GrillQuery, location="query")
    @app.doc(tags=["grill"], summary="Grill station state and orderable items")
    def grill_state(query_data):
        svc = _svc()
        location = query_data["location"]
        _station, meal, snap = _grill_snapshot(svc, location)
        items = _grill_items_for_service(svc, location, meal)
        mains, condiments = split_grill(items)
        for m in mains:
            m["condiments"] = condiment_ids_for(m["name"], condiments)
        user = current_user()
        mine = svc.store.user_open_grill_order(user.sub) if user else None
        walk_up = "walk_up" in (snap["why_not"] or [])

        dining_allowed, dining_reason = (None, None)
        if user is not None and meal is not None:
            dining_allowed, dining_reason = _may_dine(user, location, meal)

        next_service_min = None
        if meal is None:
            now = dt.datetime.now(LOCAL_TZ)
            starts = [declaration_closes_at(location, now.date(), m, LOCAL_TZ)
                      for m in (0, 1, 2)]
            coming = [s for s in starts if s > now]
            if coming:
                next_service_min = max(
                    0, int((min(coming) - now).total_seconds() // 60))

        return {
            "location": location, **snap,
            "next_service_min": next_service_min,
            "dining_allowed": dining_allowed,
            "dining_reason": dining_reason,
            "walk_up_message": _GRILL_REFUSALS["walk_up"][1] if walk_up else None,
            "mains": mains, "condiments": condiments,
            "your_order": _order_view(mine) if mine else None,
        }

    @app.post(f"{API}/grill/orders")
    @app.input(GrillOrderIn)
    @login_required
    @app.doc(tags=["grill"], summary="Order from the grill (requires sign-in)")
    def place_grill(json_data):
        svc = _svc()
        user = current_user()
        location = json_data["location_id"]

        _station, meal, snap = _grill_snapshot(svc, location)
        for reason in snap["why_not"] or []:
            code, message = _GRILL_REFUSALS[reason]
            abort(code, message)

        allowed, _why = _may_dine(user, location, meal)
        if not allowed:
            abort(403, MSG_GRILL_NOT_PERMITTED)

        if svc.store.user_open_grill_order(user.sub):
            abort(409, MSG_ORDER_IN_PROGRESS)

        items = {i["id"]: i
                 for i in _grill_items_for_service(svc, location, meal)}
        main = items.get(json_data["main_id"])
        if main is None or not split_grill([main])[0]:
            abort(422, "That item is not a grill main on today's menu here.")
        all_condiments = split_grill(list(items.values()))[1]
        applicable = set(condiment_ids_for(main["name"], all_condiments))
        chosen = []
        for cid in json_data["condiments"]:
            cond = items.get(cid)
            if cond is None or split_grill([cond])[0]:
                abort(422, "One of those condiments is not on the grill today.")
            if cid not in applicable:
                abort(422, f"{cond['name']} isn't offered with {main['name']}.")
            chosen.append(cond)
        for item in [main, *chosen]:
            if item["out"]:
                abort(409, f"{item['name']} ran out — adjust your order.")

        order = svc.store.place_grill_order(
            location, user.sub, main["id"], main["name"],
            json.dumps([{"id": c["id"], "name": c["name"]} for c in chosen]),
            _pickup_name(user.name),
        )
        return _order_view(order)

    @app.delete(f"{API}/grill/orders/<int:order_id>")
    @login_required
    @app.doc(tags=["grill"], summary="Cancel a grill order (before cooking)")
    def cancel_grill(order_id: int):
        svc = _svc()
        user = current_user()
        order = svc.store.get_grill_order(order_id)
        if order is None:
            abort(404, "No such order.")
        staff_acting = (user.affiliation == "staff"
                        and (not user.demo or kitchen_unlocked()))
        if order["user_id"] != user.sub and not staff_acting:
            abort(403, "Not your order.")
        cancelled = svc.store.cancel_grill_order(
            order_id, "staff" if staff_acting and order["user_id"] != user.sub
            else "student", by_staff=staff_acting)
        if cancelled is None:
            abort(409, "Cooking has already started — this order can no "
                       "longer be cancelled.")
        return _order_view(cancelled)

    @app.get(f"{API}/grill/station")
    @staff_required
    @app.input(GrillQuery, location="query")
    @app.doc(tags=["grill"],
             summary="Station queue — the poll IS the heartbeat (staff)")
    def grill_station_poll(query_data):
        svc = _svc()
        location = query_data["location"]
        _shift_gate(svc, location,
                    svc.store.grill_station(location, GRILL_APP_CAP_DEFAULT))
        station, orders = svc.store.grill_poll(location, GRILL_APP_CAP_DEFAULT)
        open_count = sum(1 for o in orders
                         if o["status"] in ("seen", "cooking"))
        return {
            "location": location,
            "state": station["state"],
            "app_cap": station["app_cap"],
            "open_app_orders": open_count,
            "headroom": max(0, station["app_cap"] - open_count),
            "orders": [{
                "id": o["id"], "status": o["status"],
                "who": o["pickup_name"],
                "main": o["main_name"],
                "condiments": [c["name"] for c in json.loads(o["condiments"])],
                "placed_at": o["placed_at"],
            } for o in orders],
        }

    @app.post(f"{API}/grill/station")
    @staff_required
    @app.input(GrillStationIn)
    @app.doc(tags=["grill"],
             summary="Set grill state and walk-up headroom (staff)")
    def grill_station_set(json_data):
        svc = _svc()
        location = json_data["location_id"]
        if json_data.get("state") == "closed":
            station = _close_grill(svc, location)
            if json_data.get("app_cap") is not None:
                station = svc.store.set_grill_station(
                    location, GRILL_APP_CAP_DEFAULT,
                    app_cap=json_data["app_cap"])
        else:
            station = svc.store.set_grill_station(
                location, GRILL_APP_CAP_DEFAULT,
                state=json_data.get("state"), app_cap=json_data.get("app_cap"),
            )
        return {"location": station["location_id"], "state": station["state"],
                "app_cap": station["app_cap"]}

    @app.post(f"{API}/grill/orders/<int:order_id>/advance")
    @staff_required
    @app.input(GrillAdvanceIn)
    @app.doc(tags=["grill"], summary="Advance an order one step (staff)")
    def grill_advance(order_id: int, json_data):
        svc = _svc()
        order = svc.store.advance_grill_order(order_id, json_data["to"])
        if order is None:
            abort(409, "That step is not available for this order.")
        if order["status"] == "cooking":
            _notify_user(svc, order["user_id"], _ORDER_MSG["cooking"])
        elif order["status"] == "ready":
            _notify_user(svc, order["user_id"], _ORDER_MSG["ready"])
        return _order_view(order)


    def _report_payload(kind: str, rows: list[dict], columns: list[str],
                        fmt: str, meta: dict):
        if fmt == "csv":
            stamp = _today().replace("-", "")
            return app.response_class(
                reports.to_csv(rows, columns),
                mimetype="text/csv",
                headers={"Content-Disposition":
                         f'attachment; filename="veritaste-{kind}-{stamp}.csv"'},
            )
        return {
            "report": kind, **meta, "row_count": len(rows), "rows": rows,
            "generated_at": dt.datetime.utcnow().isoformat(),
            "disclaimer": DISCLAIMER,
        }

    def _hall_names(svc) -> dict[int, str]:
        try:
            return {loc["id"]: loc["name"] for loc in svc.dining.locations()}
        except Exception:
            return {}

    def _dish_names(svc, rows: list[dict]) -> tuple[dict[int, str], bool]:
        ids = sorted({r["recipe_id"] for r in rows})
        if not ids:
            return {}, False
        if len(ids) > reports.NAME_RESOLVE_MAX:
            return {}, True
        fetched = svc.dining.recipes(ids)
        return {rid: rec.get("name") for rid, rec in fetched.items()}, False

    def _report_window(query_data) -> tuple[str, str]:
        to_s = query_data["date_to"] or _today()
        try:
            to_d = _parse_date(to_s)
            from_s = (query_data["date_from"]
                      or (to_d - dt.timedelta(days=30)).isoformat())
            from_d = _parse_date(from_s)
        except ValueError:
            abort(422, "Dates are YYYY-MM-DD.")
        if from_d > to_d:
            abort(422, "`from` is after `to`.")
        return from_s, to_s

    @app.get(f"{API}/reports/ratings")
    @report_key_required
    @app.input(RatingsReportQuery, location="query")
    @app.doc(tags=["reports"],
             summary="Dish ratings by hall — aggregate export (report key)")
    def report_ratings(query_data):
        svc = _svc()
        days = query_data["days"]
        rows = svc.store.ratings_report(query_data["location"], days)
        names, truncated = _dish_names(svc, rows)
        halls = _hall_names(svc)
        for r in rows:
            r["name"] = names.get(r["recipe_id"])
            r["hall"] = halls.get(r["location_id"])
            r["trend"] = reports.trend(r["recent_average"], r["prior_average"])
            r["small_sample"] = (r["recent_votes"] or 0) < reports.SMALL_SAMPLE_MIN
        columns = ["recipe_id", "name", "location_id", "hall", "votes",
                   "average", "recent_votes", "recent_average", "prior_average",
                   "trend", "small_sample", "last_rated"]
        meta = {"window_days": days, "location": query_data["location"],
                "small_sample_below": reports.SMALL_SAMPLE_MIN}
        if truncated:
            meta["note"] = (f"More than {reports.NAME_RESOLVE_MAX} distinct "
                            "dishes; names omitted to spare the upstream API.")
        return _report_payload("ratings", rows, columns,
                               query_data["format"], meta)

    @app.get(f"{API}/reports/attendance")
    @report_key_required
    @app.input(ServiceReportQuery, location="query")
    @app.doc(tags=["reports"],
             summary="Declared attendance by service — aggregate export (report key)")
    def report_attendance(query_data):
        svc = _svc()
        from_s, to_s = _report_window(query_data)
        rows = svc.store.attendance_report(query_data["location"], from_s, to_s)
        halls = _hall_names(svc)
        for r in rows:
            r["date"] = r.pop("served_on")
            r["meal_name"] = MEAL_NAMES.get(r["meal"])
            r["hall"] = halls.get(r["location_id"])
        columns = ["date", "meal", "meal_name", "location_id", "hall",
                   "declared_attending", "declared_absent", "responses"]
        meta = {"from": from_s, "to": to_s, "location": query_data["location"]}
        return _report_payload("attendance", rows, columns,
                               query_data["format"], meta)

    @app.get(f"{API}/reports/waste")
    @report_key_required
    @app.input(ServiceReportQuery, location="query")
    @app.doc(tags=["reports"],
             summary="Prepared vs wasted by dish — aggregate export (report key)")
    def report_waste(query_data):
        svc = _svc()
        from_s, to_s = _report_window(query_data)
        rows = svc.store.waste_report(query_data["location"], from_s, to_s)
        names, truncated = _dish_names(svc, rows)
        halls = _hall_names(svc)
        for r in rows:
            r["name"] = names.get(r["recipe_id"])
            r["hall"] = halls.get(r["location_id"])
        columns = ["recipe_id", "name", "location_id", "hall", "services",
                   "prepared_lb", "wasted_lb", "waste_rate"]
        meta = {"from": from_s, "to": to_s, "location": query_data["location"]}
        if truncated:
            meta["note"] = (f"More than {reports.NAME_RESOLVE_MAX} distinct "
                            "dishes; names omitted to spare the upstream API.")
        return _report_payload("waste", rows, columns,
                               query_data["format"], meta)


    @app.get("/")
    @app.doc(hide=True)
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/signin")
    @app.doc(hide=True)
    def signin_page():
        return send_from_directory(WEB_DIR, "signin.html")

    @app.get("/display")
    @app.doc(hide=True)
    def display_page():
        return send_from_directory(WEB_DIR, "display.html")

    @app.get("/staffunlock")
    @app.doc(hide=True)
    def staffunlock_page():
        return send_from_directory(WEB_DIR, "staffunlock.html")


app = create_app()
