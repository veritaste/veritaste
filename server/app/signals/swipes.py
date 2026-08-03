from __future__ import annotations

from abc import ABC, abstractmethod


class SwipePresenceProvider(ABC):

    @abstractmethod
    def present_users(self, location_id: int, served_on: str,
                      meal: int) -> set[str]:
        ...


class NoSwipeFeed(SwipePresenceProvider):

    def present_users(self, location_id: int, served_on: str,
                      meal: int) -> set[str]:
        raise NotImplementedError(
            "No swipe feed exists. When Harvard grants one, implement a "
            "provider here and the last-call send gets smart without "
            "redesign."
        )
