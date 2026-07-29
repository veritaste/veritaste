from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .houses import ANNENBERG_LOCATION, BY_KEY, HOUSES, OPEN_LOCATIONS, House, Neighborhood

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
BREAKFAST, LUNCH, DINNER = 0, 1, 2


class Access(str, Enum):
    OPEN = "open"
    GUEST_ONLY = "guest_only"
    RESIDENTS_ONLY = "residents_only"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    CONFIRMED = "confirmed"
    REPORTED = "reported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Rule:
    house_keys: tuple[str, ...] | None
    meals: tuple[int, ...] | None
    weekdays: tuple[int, ...] | None
    access: Access
    reason: str
    source: str
    confidence: Confidence = Confidence.CONFIRMED
    except_houses: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, house: str, meal: int, weekday: int) -> bool:
        if house in self.except_houses:
            return False
        if self.house_keys is not None and house not in self.house_keys:
            return False
        if self.meals is not None and meal not in self.meals:
            return False
        if self.weekdays is not None and weekday not in self.weekdays:
            return False
        return True


RULES: tuple[Rule, ...] = (
    Rule(
        house_keys=("yard",), meals=(LUNCH, DINNER), weekdays=None,
        access=Access.RESIDENTS_ONLY,
        reason="Annenberg serves first-year students only at lunch and dinner. "
               "Breakfast is open to all students.",
        source="dining.harvard.edu/undergraduate-dining/annenberg",
        confidence=Confidence.REPORTED,
    ),

    Rule(
        house_keys=("currier",), meals=(DINNER,), weekdays=(MON, THU, SUN),
        access=Access.RESIDENTS_ONLY,
        reason="Currier closes its dining hall to non-Currier students on Monday, "
               "Thursday and Sunday nights.",
        source="The Harvard Crimson, 2023-10-06",
        confidence=Confidence.CONFIRMED,
    ),

    Rule(
        house_keys=None, meals=(DINNER,), weekdays=(THU,),
        access=Access.RESIDENTS_ONLY,
        reason="Community Night. Since 2014 students eat in their own House on "
               "Thursday evenings. Quad residents may use their designated "
               "sister River House.",
        source="The Harvard Crimson, 2014-09-11",
        confidence=Confidence.CONFIRMED,
        except_houses=("currier", "yard"),
    ),

    Rule(
        house_keys=("eliot", "lowell"), meals=(LUNCH,), weekdays=None,
        access=Access.GUEST_ONLY,
        reason="Eliot (The Inn) and Lowell allow a resident to bring one guest at "
               "lunch, and only between 1:00 and 2:00pm.",
        source="The Harvard Crimson, 2025-10-03",
        confidence=Confidence.CONFIRMED,
    ),

    Rule(
        house_keys=("quincy", "adams"), meals=None, weekdays=None,
        access=Access.OPEN,
        reason="Quincy and Adams residents may bring a guest whenever they choose.",
        source="The Harvard Crimson, 2025-10-03",
        confidence=Confidence.CONFIRMED,
    ),
)

UNDOCUMENTED = ("dunster", "mather", "kirkland", "leverett", "winthrop",
                "cabot", "pforzheimer")


@dataclass(frozen=True)
class Verdict:
    house_key: str
    house_name: str
    location_id: int
    location_name: str
    access: Access
    reason: str
    source: str
    confidence: Confidence
    is_home: bool


def evaluate(house: House, meal: int, on: date, viewer_house: str | None) -> Verdict:
    weekday = on.weekday()
    is_home = viewer_house == house.key or (
        viewer_house is not None
        and house.location_id == BY_KEY[viewer_house].location_id
    )

    if is_home:
        return Verdict(
            house.key, house.name, house.location_id, house.location_name,
            Access.OPEN, "Your own dining hall.", "—", Confidence.CONFIRMED, True,
        )

    quad_note = ""
    if viewer_house and BY_KEY[viewer_house].neighborhood is Neighborhood.QUAD \
            and house.neighborhood is Neighborhood.RIVER:
        quad_note = (" Quad residents may always eat at their designated sister "
                     "River House; confirm which one applies to you.")

    for rule in RULES:
        if rule.matches(house.key, meal, weekday):
            return Verdict(
                house.key, house.name, house.location_id, house.location_name,
                rule.access, rule.reason + quad_note, rule.source,
                rule.confidence, False,
            )

    if house.key in UNDOCUMENTED:
        return Verdict(
            house.key, house.name, house.location_id, house.location_name,
            Access.UNKNOWN,
            "We could not confirm this House's current interhouse rule. Check "
            "with the House before relying on it." + quad_note,
            "not established", Confidence.UNKNOWN, False,
        )

    return Verdict(
        house.key, house.name, house.location_id, house.location_name,
        Access.OPEN, "No restriction recorded for this meal." + quad_note,
        "no matching rule", Confidence.REPORTED, False,
    )


def where_can_i_eat(meal: int, on: date, viewer_house: str | None) -> list[Verdict]:
    seen: set[int] = set()
    out: list[Verdict] = []
    for house in HOUSES:
        if house.location_id in seen:
            continue
        seen.add(house.location_id)
        out.append(evaluate(house, meal, on, viewer_house))

    order = {Access.OPEN: 0, Access.GUEST_ONLY: 1,
             Access.UNKNOWN: 2, Access.RESIDENTS_ONLY: 3}
    out.sort(key=lambda v: (not v.is_home, order[v.access], v.house_name))
    return out


def open_dining_locations() -> list[dict]:
    return [{"location_id": lid, "name": name}
            for lid, name in sorted(OPEN_LOCATIONS.items(), key=lambda kv: kv[1])]
