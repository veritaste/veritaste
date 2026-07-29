from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Neighborhood(str, Enum):
    RIVER = "river"
    QUAD = "quad"
    YARD = "yard"
    NON_RESIDENTIAL = "non_residential"


@dataclass(frozen=True)
class House:
    key: str
    name: str
    neighborhood: Neighborhood
    location_id: int
    location_name: str
    sister_house: str | None = None


HOUSES: tuple[House, ...] = (
    House("adams",     "Adams House",      Neighborhood.RIVER,  9, "Adams House"),
    House("dunster",   "Dunster House",    Neighborhood.RIVER,  7, "Dunster and Mather House"),
    House("mather",    "Mather House",     Neighborhood.RIVER,  7, "Dunster and Mather House"),
    House("eliot",     "Eliot House",      Neighborhood.RIVER, 14, "Eliot and Kirkland House"),
    House("kirkland",  "Kirkland House",   Neighborhood.RIVER, 14, "Eliot and Kirkland House"),
    House("leverett",  "Leverett House",   Neighborhood.RIVER, 16, "Leverett House"),
    House("lowell",    "Lowell House",     Neighborhood.RIVER, 15, "Lowell and Winthrop House"),
    House("winthrop",  "Winthrop House",   Neighborhood.RIVER, 15, "Lowell and Winthrop House"),
    House("quincy",    "Quincy House",     Neighborhood.RIVER,  8, "Quincy House"),

    House("cabot",     "Cabot House",       Neighborhood.QUAD,  5, "Cabot and Pforzheimer House"),
    House("pforzheimer","Pforzheimer House",Neighborhood.QUAD,  5, "Cabot and Pforzheimer House"),
    House("currier",   "Currier House",     Neighborhood.QUAD, 38, "Currier House"),

    House("yard",      "First-Year (Yard)", Neighborhood.YARD, 30, "Annenberg Hall"),
)

BY_KEY: dict[str, House] = {h.key: h for h in HOUSES}

OPEN_LOCATIONS: dict[int, str] = {
    29: "Fly-By",
    4: "Dudley Cafe",
    27: "Sebastian's Cafe",
    54: "Northwest Cafe",
    41: "Chauhaus at the GSD",
    3: "Cronkhite Dining Room",
}

ANNENBERG_LOCATION = 30


def houses_at(location_id: int) -> list[House]:
    return [h for h in HOUSES if h.location_id == location_id]


def house_locations() -> list[tuple[int, str]]:
    seen: dict[int, str] = {}
    for h in HOUSES:
        seen.setdefault(h.location_id, h.location_name)
    return sorted(seen.items(), key=lambda kv: kv[1])
