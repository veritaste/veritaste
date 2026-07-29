from __future__ import annotations

import argparse
import hashlib

from app.sources import DiningSource
from app.store import build_store

CATEGORY_WASTE = {
    "dessert": 0.04,
    "bakery": 0.06,
    "pizza": 0.05,
    "grill": 0.07,
    "breakfast": 0.10,
    "entree": 0.11,
    "halal": 0.12,
    "deli": 0.13,
    "sand": 0.13,
    "soup": 0.22,
    "chili": 0.20,
    "starch": 0.26,
    "potato": 0.24,
    "rice": 0.28,
    "plant protein": 0.30,
    "veg, vegan": 0.31,
    "salad": 0.35,
    "vegetable": 0.42,
    "beverage": 0.08,
}
DEFAULT_WASTE = 0.18


def _base_rate(category: str) -> float:
    low = category.lower()
    for key, rate in CATEGORY_WASTE.items():
        if key in low:
            return rate
    return DEFAULT_WASTE


def _rate_for(recipe: dict, category: str) -> float:
    base = _base_rate(category)

    low = str(recipe.get("name") or "").lower()
    if any(w in low for w in ("steamed", "boiled", "plain", "brussels", "kale",
                              "beet", "lentil", "tofu", "quinoa")):
        base += 0.12
    if any(w in low for w in ("cheese", "sauce", "fried", "roasted", "bacon",
                              "chocolate", "butter", "crispy")):
        base -= 0.06

    digest = hashlib.sha256(str(recipe.get("id")).encode()).digest()
    jitter = (digest[0] / 255.0 - 0.5) * 0.14
    return max(0.02, min(0.65, base + jitter))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default="2026-04-15")
    ap.add_argument("--location", type=int, default=30)
    ap.add_argument("--meals", default="0,1,2")
    args = ap.parse_args()

    store = build_store()
    store.init_schema()
    dining = DiningSource(store)

    total = 0
    try:
        cats = {c["id"]: c["name"] for c in dining.categories()}

        for meal in [int(m) for m in args.meals.split(",")]:
            rows, _status = dining.menu_rows(args.date, args.location, meal)
            recipe_ids = sorted({r["recipe"] for r in rows})
            if not recipe_ids:
                print(f"  meal {meal}: no menu rows")
                continue
            recipes = dining.recipes(recipe_ids)

            category_of: dict[int, str] = {}
            for row in rows:
                category_of.setdefault(row["recipe"], cats.get(row["category"], ""))

            for rid, recipe in recipes.items():
                category = category_of.get(rid, "")
                rate = _rate_for(recipe, category)
                prepared = 40.0 + (rid % 37) * 2.5
                store.add_waste_observation(
                    recipe_id=rid,
                    location_id=args.location,
                    served_on=args.date,
                    meal=meal,
                    prepared_lb=round(prepared, 1),
                    wasted_lb=round(prepared * rate, 2),
                    source="winnow-mock",
                )
                total += 1
            print(f"  meal {meal}: {len(recipes)} observations")
    finally:
        dining.close()
        store.close()

    print(f"seeded {total} waste observations for {args.date} location {args.location}")


if __name__ == "__main__":
    main()
