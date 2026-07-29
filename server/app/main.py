from __future__ import annotations

import datetime as dt
import logging
import os
import sys

from apiflask import APIFlask, Schema, abort
from apiflask.fields import Boolean, Integer, String
from apiflask.validators import Length, OneOf, Range
from flask import current_app, g, jsonify, request, send_from_directory

from . import __version__
from .auth import SESSION_COOKIE, User, current_user, issue_session, login_required
from .config import CACHE_TTL_HOURS, DB_PATH, ECS_APIKEY, STORE_BACKEND, WEB_DIR
from .reference import (BY_KEY, BY_NETID, HOUSES, is_brunch_day, meal_name,
                        open_dining_locations, public_list, where_can_i_eat)
from .signals import SimulatedLineProvider, spice_for
from .sources import MEAL_NAMES, DiningSource
from .store import build_store

API = "/api/v1"

DISCLAIMER = (
    "This is not affiliated with or endorsed by Harvard University or HUDS."
)


class MenuQuery(Schema):
    date = String(load_default=lambda: dt.date.today().isoformat())
    location = Integer(required=True)
    meal = Integer(load_default=1, validate=Range(min=0, max=2))


class LineQuery(Schema):
    date = String(load_default=lambda: dt.date.today().isoformat())


class InterhouseQuery(Schema):
    date = String(load_default=lambda: dt.date.today().isoformat())
    meal = Integer(load_default=2, validate=Range(min=0, max=2))
    house = String(load_default=None, allow_none=True)


class AttendanceQuery(Schema):
    location = Integer(required=True)
    date = String(load_default=lambda: dt.date.today().isoformat())
    meal = Integer(load_default=2, validate=Range(min=0, max=2))


class RatingIn(Schema):
    recipe_id = Integer(required=True)
    score = Integer(required=True, validate=Range(min=1, max=5))
    location_id = Integer(load_default=None, allow_none=True)
    served_on = String(load_default=None, allow_none=True)
    comment = String(load_default=None, allow_none=True, validate=Length(max=1000))


class AttendanceIn(Schema):
    location_id = Integer(required=True)
    meal = Integer(load_default=2, validate=Range(min=0, max=2))
    served_on = String(load_default=None, allow_none=True)
    attending = Boolean(required=True)


class SignInIn(Schema):
    netid = String(required=True, validate=OneOf(list(BY_NETID)))


def _svc():
    return current_app.extensions["veritaste"]


def _today() -> str:
    return dt.date.today().isoformat()


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

        base = {
            "date": date, "location": location, "meal": meal,
            "meal_name": meal_name(location, meal, is_brunch=brunch),
            "freshness": freshness,
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
        ratings = svc.store.rating_summary(recipe_ids)
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
    @app.doc(tags=["dining"], summary="One dish — the QR/NFC scan target, no login")
    def recipe_detail(recipe_id: int):
        svc = _svc()
        try:
            recipe = svc.dining.recipe(recipe_id)
        except Exception:
            abort(404, f"No recipe {recipe_id}")

        spice = spice_for(recipe)
        rating = svc.store.rating_summary([recipe_id]).get(recipe_id)
        consumption = svc.store.consumption_signals([recipe_id]).get(recipe_id)
        return {
            **recipe,
            "spice": {"level": spice.level, "curated": spice.curated, "basis": spice.basis},
            "rating": None if rating is None else {
                "average": rating.average, "count": rating.count,
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
        svc.store.add_rating(
            recipe_id=json_data["recipe_id"], score=json_data["score"],
            user_id=user.sub, location_id=json_data.get("location_id"),
            served_on=json_data.get("served_on"), comment=json_data.get("comment"),
        )
        got = svc.store.rating_summary([json_data["recipe_id"]]).get(json_data["recipe_id"])
        return {
            "recorded": True, "recipe_id": json_data["recipe_id"],
            "average": got.average if got else float(json_data["score"]),
            "count": got.count if got else 1,
        }, 201

    @app.post(f"{API}/attendance")
    @app.input(AttendanceIn)
    @login_required
    @app.doc(tags=["feedback"],
             summary="Declare whether you are coming (requires sign-in)")
    def set_attendance(json_data):
        user = current_user()
        svc = _svc()
        served_on = json_data.get("served_on") or _today()
        svc.store.set_attendance_intent(
            user_id=user.sub, location_id=json_data["location_id"],
            served_on=served_on, meal=json_data["meal"],
            attending=json_data["attending"],
        )
        yes, no = svc.store.attendance_counts(
            json_data["location_id"], served_on, json_data["meal"]
        )
        return {
            "recorded": True, "attending": json_data["attending"],
            "declared_attending": yes, "declared_absent": no,
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
            sub=account.sub, name=account.name,
            principal=account.principal, affiliation=account.affiliation,
            house_key=account.house_key if account.house_key in BY_KEY else None,
            demo=True,
        )
        resp = jsonify({
            "signed_in": True, "name": user.name, "netid": account.netid,
            "affiliation": user.affiliation, "house_key": user.house_key,
            "demo": True,
        })
        resp.set_cookie(SESSION_COOKIE, issue_session(user),
                        httponly=True, samesite="Lax", max_age=12 * 3600)
        return resp

    @app.post(f"{API}/auth/signout")
    @app.doc(tags=["auth"], summary="Clear the session")
    def signout():
        resp = jsonify({"signed_in": False})
        resp.delete_cookie(SESSION_COOKIE)
        return resp


    @app.get("/")
    @app.doc(hide=True)
    def index():
        return send_from_directory(WEB_DIR, "index.html")


app = create_app()
