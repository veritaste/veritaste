from __future__ import annotations

import datetime as dt
import logging
import os
import secrets
import sys

from apiflask import APIFlask, Schema, abort
from apiflask.fields import Boolean, Integer, String
from apiflask.validators import Length, OneOf, Range
from flask import current_app, g, jsonify, request, send_from_directory

from . import __version__, push
from .auth import SESSION_COOKIE, User, current_user, issue_session, login_required
from .config import (CACHE_TTL_HOURS, DB_PATH, DEMO_MODE, ECS_APIKEY, LOCAL_TZ,
                     MODE, RATING_RECENT_DAYS, STORE_BACKEND, TIMEZONE, WEB_DIR)
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
    comment = String(load_default=None, allow_none=True, validate=Length(max=1000))


class RecipeQuery(Schema):
    location = Integer(load_default=None, allow_none=True)


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


def _item(recipe: dict, rating, consumption) -> dict:
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
    }


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
                _item(recipe, ratings.get(recipe["id"]), consumption.get(recipe["id"]))
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

        return {
            **recipe,
            "spice": {"level": spice.level, "curated": spice.curated, "basis": spice.basis},
            "your_rating": yours,
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
        r = _svc().lines.current(location_id)
        return {
            "location": r.location_id,
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
        series = _svc().lines.typical_day(location_id, on)
        return {
            "location": location_id, "date": on.isoformat(),
            "weekday": on.strftime("%A"),
            "series": [{"time": t.strftime("%H:%M"), "busyness": v} for t, v in series],
            "simulated": True,
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
            served_on=json_data.get("served_on"), comment=json_data.get("comment"),
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
        }

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


    @app.get("/")
    @app.doc(hide=True)
    def index():
        return send_from_directory(WEB_DIR, "index.html")


app = create_app()
