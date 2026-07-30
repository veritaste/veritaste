from __future__ import annotations

GRANTS: dict[str, int] = {
    "attendance": 25,
    "rating": 10,
}

LABELS: dict[str, str] = {
    "attendance": "Told the kitchen your plans",
    "rating": "Rated a dish",
}

DAILY_CAP_CENTS = 100


DAY_SCOPED = -1


def format_cents(cents: int) -> str:
    return f"${cents / 100:.2f}"


def settlement_note() -> str:
    return (
        "In production Veritaste would hand HUDS a per-student total for the "
        "period and HUDS would credit real BoardPlus through Transact Campus. "
        "Settlement stays with HUDS: nothing here can move money, and no "
        "redemption exists in this prototype."
    )
