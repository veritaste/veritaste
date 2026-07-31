from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CachedBlob:

    key: str
    body: str
    digest: str
    fetched_at: datetime
    changed_at: datetime


@dataclass(frozen=True)
class RatingSummary:

    recipe_id: int
    count: int
    average: float
    recent_count: int
    recent_average: float | None


@dataclass(frozen=True)
class ConsumptionSignal:

    recipe_id: int
    rate: float
    observations: int


@dataclass(frozen=True)
class RewardGrant:

    kind: str
    cents: int
    location_id: int
    served_on: str
    meal: int
    granted_on: str
    created_at: str


@dataclass(frozen=True)
class RewardSummary:

    pending_cents: int
    grant_count: int
    day_cents: int
    recent: tuple[RewardGrant, ...]


@dataclass(frozen=True)
class PushSub:

    endpoint: str
    user_sub: str
    affiliation: str
    house_key: str | None
    p256dh: str
    auth: str

    def as_subscription_info(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


class Store(ABC):


    @abstractmethod
    def init_schema(self) -> None:
        ...

    @abstractmethod
    def close(self) -> None: ...


    @abstractmethod
    def get_cached(self, key: str) -> CachedBlob | None: ...

    @abstractmethod
    def put_cached(self, key: str, body: str, digest: str, now: datetime) -> None:
        ...

    @abstractmethod
    def touch_cached(self, key: str, now: datetime) -> None:
        ...


    @abstractmethod
    def add_rating(
        self,
        recipe_id: int,
        score: int,
        user_id: str,
        location_id: int,
        served_on: str | None,
        comment: str | None,
        recent_days: int,
    ) -> bool:
        ...

    @abstractmethod
    def rating_summary(
        self, recipe_ids: list[int], location_id: int | None, recent_days: int
    ) -> dict[int, RatingSummary]:
        ...

    @abstractmethod
    def user_rating(
        self, user_id: str, recipe_id: int, location_id: int
    ) -> int | None:
        ...

    @abstractmethod
    def rating_trend(
        self, recipe_id: int, location_id: int | None, buckets: int, days: int
    ) -> list[tuple[str, int, float]]:
        ...


    @abstractmethod
    def add_waste_observation(
        self,
        recipe_id: int,
        location_id: int,
        served_on: str,
        meal: int,
        prepared_lb: float,
        wasted_lb: float,
        source: str,
    ) -> None:
        ...

    @abstractmethod
    def consumption_signals(self, recipe_ids: list[int]) -> dict[int, ConsumptionSignal]:
        ...


    @abstractmethod
    def set_attendance_intent(
        self, user_id: str, location_id: int, served_on: str, meal: int, attending: bool
    ) -> None:
        ...

    @abstractmethod
    def attendance_counts(
        self, location_id: int, served_on: str, meal: int
    ) -> tuple[int, int]:
        ...


    @abstractmethod
    def grant_reward(
        self,
        user_id: str,
        location_id: int,
        served_on: str,
        meal: int,
        kind: str,
        cents: int,
        granted_on: str,
    ) -> int:
        ...

    @abstractmethod
    def reward_summary(self, user_id: str, on_date: str) -> RewardSummary:
        ...


    @abstractmethod
    def put_push_sub(self, sub: PushSub) -> None:
        ...

    @abstractmethod
    def delete_push_sub(self, endpoint: str) -> None:
        ...

    @abstractmethod
    def push_subs(
        self, user_id: str | None = None, affiliation: str | None = None
    ) -> list[PushSub]:
        ...


    @abstractmethod
    def create_report_key(self, label: str, scopes: str, key_hash: str) -> int:
        ...

    @abstractmethod
    def report_keys(self) -> list[dict]:
        ...

    @abstractmethod
    def revoke_report_key(self, key_id: int) -> bool:
        ...

    @abstractmethod
    def verify_report_key(self, key_hash: str) -> dict | None:
        ...

    @abstractmethod
    def ratings_report(self, location_id: int | None, days: int) -> list[dict]:
        ...

    @abstractmethod
    def attendance_report(
        self, location_id: int | None, date_from: str, date_to: str
    ) -> list[dict]:
        ...

    @abstractmethod
    def waste_report(
        self, location_id: int | None, date_from: str, date_to: str
    ) -> list[dict]:
        ...


    @abstractmethod
    def set_stock(self, location_id: int, recipe_id: int, status: str,
                  note: str | None, user_id: str, marked_on: str) -> None:
        ...

    @abstractmethod
    def clear_stock(self, location_id: int, recipe_id: int, user_id: str) -> bool:
        ...

    @abstractmethod
    def stock_marks(self, location_id: int, marked_on: str) -> list[dict]:
        ...
