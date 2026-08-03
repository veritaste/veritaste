from __future__ import annotations

import argparse
import os
from abc import ABC, abstractmethod

from app.reference.houses import HOUSES
from app.store import build_store

HALL_NAMES = {h.location_id: h.location_name for h in HOUSES}
HALL_NAMES[30] = "Annenberg Hall"


class SurveyDirectory(ABC):

    @abstractmethod
    def recipients_of(self, location_id: int) -> list[str]:
        ...


class NoDirectory(SurveyDirectory):

    def recipients_of(self, location_id: int) -> list[str]:
        raise NotImplementedError(
            "No student directory exists — the app stores no email "
            "addresses. Implement SurveyDirectory against the real IdP; "
            "composition is already real and waiting."
        )


def armed() -> bool:
    return os.environ.get("VERITASTE_SURVEYMAIL", "").lower() in (
        "1", "on", "true", "yes")


def compose(store) -> list[tuple[int, str, str]]:
    out = []
    for loc, name in sorted(HALL_NAMES.items()):
        if store.queue_picks(loc):
            out.append((loc, name, f"/?pane=survey&hall={loc}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print which halls would send, and to what link")
    args = ap.parse_args()

    if not armed() and not args.dry_run:
        print("VERITASTE_SURVEYMAIL is not set — built dark, nothing sent.")
        return 0

    store = build_store()
    halls = compose(store)
    if not halls:
        print("No hall has a composed survey; nothing goes out — "
              "by rule, not by failure.")
        return 0

    print(f"{len(halls)} hall(s) with a composed survey:")
    for loc, name, link in halls:
        print(f"  {name}: {link}")
    if args.dry_run:
        print("Dry run — nothing sent.")
        return 0

    directory = NoDirectory()
    for loc, name, link in halls:
        directory.recipients_of(loc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
