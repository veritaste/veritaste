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


@dataclass(frozen=True)
class ConsumptionSignal:

    recipe_id: int
    rate: float
    observations: int


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
        location_id: int | None,
        served_on: str | None,
        comment: str | None,
    ) -> None:
        ...

    @abstractmethod
    def rating_summary(self, recipe_ids: list[int]) -> dict[int, RatingSummary]: ...


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
