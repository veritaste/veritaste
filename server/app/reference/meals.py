from __future__ import annotations

from datetime import date, datetime, time, tzinfo

BREAKFAST, LUNCH, DINNER = 0, 1, 2

DECLARATION_CUTOFF: dict[int, time] = {
    BREAKFAST: time(7, 30),
    LUNCH: time(11, 30),
    DINNER: time(17, 0),
}

LOCATION_CUTOFF: dict[int, dict[int, time]] = {
    30: {DINNER: time(16, 30)},
    29: {0: time(11, 0)},
}


SERVICE_END: dict[int, time] = {
    BREAKFAST: time(10, 30),
    LUNCH: time(14, 0),
    DINNER: time(19, 30),
}

LOCATION_END: dict[int, dict[int, time]] = {
    29: {0: time(14, 30)},
}


def service_ends_at(
    location_id: int, on: date, meal: int, tz: tzinfo
) -> datetime | None:
    override = LOCATION_END.get(location_id, {})
    at = override.get(meal) or SERVICE_END.get(meal)
    return None if at is None else datetime.combine(on, at, tzinfo=tz)


def service_status(
    location_id: int, on: date, meal: int, tz: tzinfo, now: datetime
) -> str:
    if now < declaration_closes_at(location_id, on, meal, tz):
        return "upcoming"
    end = service_ends_at(location_id, on, meal, tz)
    if end is not None and now < end:
        return "serving"
    return "over"


def declaration_closes_at(
    location_id: int, on: date, meal: int, tz: tzinfo
) -> datetime:
    override = LOCATION_CUTOFF.get(location_id, {})
    at = override.get(meal) or DECLARATION_CUTOFF.get(meal, time(0, 0))
    return datetime.combine(on, at, tzinfo=tz)

DEFAULT_MEAL_NAMES: dict[int, str] = {
    BREAKFAST: "Breakfast",
    LUNCH: "Lunch",
    DINNER: "Dinner",
}

LOCATION_MEAL_NAMES: dict[int, dict[int, str]] = {
    29: {0: "Lunch"},
    27: {0: "Lunch"},
    54: {1: "All day"},
}

BRUNCH_CATEGORY = 9


def meal_name(location_id: int, meal: int, *, is_brunch: bool = False) -> str:
    override = LOCATION_MEAL_NAMES.get(location_id)
    if override and meal in override:
        return override[meal]
    if is_brunch and meal == LUNCH:
        return "Brunch"
    return DEFAULT_MEAL_NAMES.get(meal, f"Meal {meal}")


def is_brunch_day(rows: list[dict]) -> bool:
    return any(r.get("category") == BRUNCH_CATEGORY for r in rows)
