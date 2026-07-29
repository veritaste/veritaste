from __future__ import annotations

BREAKFAST, LUNCH, DINNER = 0, 1, 2

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
