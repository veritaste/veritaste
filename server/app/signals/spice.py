from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LEVEL = 3


@dataclass(frozen=True)
class SpiceLevel:
    level: int
    curated: bool
    basis: str


CURATED: dict[int, tuple[int, str]] = {
    10010: (2, "Thai red curry paste and chili sauce"),
    10012: (2, "Buffalo sauce base"),
}

_PATTERNS: list[tuple[re.Pattern, int, str]] = [
    (re.compile(r"ghost pepper|habanero|scotch bonnet|carolina reaper", re.I), 3,
     "very hot chili variety"),
    (re.compile(r"cayenne|chipotle|sriracha|harissa|sambal|gochujang|"
                r"jalape|serrano|curry paste|red chili|chili garlic|"
                r"buffalo sauce|arrabbiata|jerk seasoning", re.I), 2,
     "medium-heat chili ingredient"),
    (re.compile(r"chili|chile|curry|salsa|pepper flake|cumin|paprika|"
                r"cajun|creole|kimchi|wasabi|horseradish|ginger", re.I), 1,
     "mild warming spice"),
]


def spice_for(recipe: dict) -> SpiceLevel:
    rid = recipe.get("id")
    if rid in CURATED:
        level, note = CURATED[rid]
        return SpiceLevel(level=level, curated=True, basis=note)

    haystack = " ".join(
        str(recipe.get(field) or "") for field in ("name", "ingredients")
    )
    for pattern, level, note in _PATTERNS:
        if pattern.search(haystack):
            return SpiceLevel(level=level, curated=False, basis=note)

    return SpiceLevel(level=0, curated=False, basis="no heat indicators found")
