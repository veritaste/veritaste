from __future__ import annotations

from dataclasses import dataclass

from .houses import BY_KEY, Neighborhood

DEMO_SCOPE = "demo.veritaste.org"


@dataclass(frozen=True)
class MockAccount:

    netid: str
    name: str
    given_name: str
    family_name: str
    affiliation: str
    house_key: str | None
    role_note: str
    huid: str

    @property
    def sub(self) -> str:
        return f"veritaste-demo-{self.netid}"

    @property
    def principal(self) -> str:
        return f"{self.netid}@{DEMO_SCOPE}"

    @property
    def house_name(self) -> str:
        house = BY_KEY.get(self.house_key or "")
        return house.name if house else "Harvard University Dining Services"

    @property
    def residence(self) -> str:
        house = BY_KEY.get(self.house_key or "")
        if house is None:
            return "HUDS"
        if house.neighborhood is Neighborhood.YARD:
            return "First-Year (Annenberg)"
        if house.neighborhood is Neighborhood.RIVER:
            return f"{house.name} (River)"
        if house.neighborhood is Neighborhood.QUAD:
            return f"{house.name} (Quad)"
        return house.name


ACCOUNTS: tuple[MockAccount, ...] = (
    MockAccount(
        netid="ajw1042", name="Amara J. Whitfield",
        given_name="Amara", family_name="Whitfield",
        affiliation="student", house_key="adams", huid="80100442",
        role_note="River House resident — sees an open Thursday only at home",
    ),
    MockAccount(
        netid="rkc2287", name="Rohan K. Chandra",
        given_name="Rohan", family_name="Chandra",
        affiliation="student", house_key="currier", huid="80233871",
        role_note="Quad resident — Currier is exempt from Community Night",
    ),
    MockAccount(
        netid="mlp3319", name="Mei-Lin Park",
        given_name="Mei-Lin", family_name="Park",
        affiliation="student", house_key="leverett", huid="80399120",
        role_note="House with unconfirmed interhouse rules — shows the honest 'unknown' state",
    ),
    MockAccount(
        netid="dso4471", name="Diego Santos-Ortiz",
        given_name="Diego", family_name="Santos-Ortiz",
        affiliation="student", house_key="yard", huid="80447715",
        role_note="First-year at Annenberg — restricted at lunch and dinner",
    ),
    MockAccount(
        netid="hks5508", name="Hannah K. Sorensen",
        given_name="Hannah", family_name="Sorensen",
        affiliation="staff", house_key=None, huid="80550803",
        role_note="HUDS staff — the forecasting and feedback view",
    ),
)

BY_NETID: dict[str, MockAccount] = {a.netid: a for a in ACCOUNTS}


def public_list() -> list[dict]:
    return [
        {
            "netid": a.netid,
            "name": a.name,
            "affiliation": a.affiliation,
            "house_key": a.house_key,
            "house_name": a.house_name,
            "residence": a.residence,
            "note": a.role_note,
        }
        for a in ACCOUNTS
    ]
