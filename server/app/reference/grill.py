from __future__ import annotations

import re

GRILL_CATEGORY = "from the grill"

_MAIN_PATTERNS = [
    r"\bburgers?\b",
    r"\bhot dogs?\b",
    r"\bgrilled chicken\b",
    r"\bchicken breast\b",
    r"\bchicken sandwich\b",
    r"\bnashville\b",
    r"\bgrilled cheese\b",
    r"\bquesadillas?\b",
    r"\bcooked to order\b",
    r"\bgrilled tofu\b",
    r"\bfrench fries\b",
]
_MAIN_RE = re.compile("|".join(_MAIN_PATTERNS), re.IGNORECASE)


def is_grill_main(name: str | None) -> bool:
    return bool(_MAIN_RE.search(name or ""))


def split_grill(items: list[dict]) -> tuple[list[dict], list[dict]]:
    mains = [i for i in items if is_grill_main(i.get("name"))]
    condiments = [i for i in items if not is_grill_main(i.get("name"))]
    return mains, condiments


_COND_TAGS = [
    (re.compile(r"hamburger rolls?", re.I), "burger_roll"),
    (re.compile(r"frankfurter|hot ?dog rolls?", re.I), "frank_roll"),
    (re.compile(r"lettuce|tomato|onion|pickle|cucumber", re.I), "produce"),
    (re.compile(r"cheese", re.I), "cheese"),
    (re.compile(r"salsa|sour cream|guacamole|pico", re.I), "mexican"),
    (re.compile(r"sauce|ketchup|mustard|mayo|aioli|bbq|relish", re.I), "sauce"),
]

_FAMILIES = [
    (re.compile(r"\bburgers?\b|\bchicken breast\b|\bgrilled chicken\b"
                r"|\bchicken sandwich\b|\bnashville\b|\bgrilled tofu\b", re.I),
     {"burger_roll", "produce", "cheese", "sauce"}),
    (re.compile(r"\bhot dogs?\b", re.I),
     {"frank_roll", "produce", "cheese", "sauce"}),
    (re.compile(r"\bgrilled cheese\b", re.I), {"produce", "sauce"}),
    (re.compile(r"\bquesadillas?\b", re.I), {"cheese", "mexican", "produce"}),
    (re.compile(r"\bcooked to order\b", re.I), {"cheese", "produce"}),
    (re.compile(r"\bfrench fries\b", re.I), {"sauce"}),
]


def _cond_tag(name: str | None) -> str | None:
    for rx, tag in _COND_TAGS:
        if rx.search(name or ""):
            return tag
    return None


def condiment_ids_for(main_name: str | None, condiments: list[dict]) -> list[int]:
    family = next(
        (tags for rx, tags in _FAMILIES if rx.search(main_name or "")), None)
    if family is None:
        return [c["id"] for c in condiments]
    out = []
    for c in condiments:
        tag = _cond_tag(c.get("name"))
        if tag is None or tag in family:
            out.append(c["id"])
    return out
