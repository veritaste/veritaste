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


    @abstractmethod
    def upsert_feedback(self, user_id: str, recipe_id: int, location_id: int,
                        served_on: str, meal: int, text: str, signed: bool,
                        signed_name: str | None, source: str) -> bool:
        ...

    @abstractmethod
    def feedback_for_location(self, location_id: int,
                              served_on: str) -> list[dict]:
        ...

    @abstractmethod
    def feedback_author(self, note_id: int) -> str | None:
        ...

    @abstractmethod
    def feedback_of(self, user_id: str, recipe_id: int, location_id: int,
                    served_on: str, meal: int) -> dict | None:
        ...

    @abstractmethod
    def feedback_counts(self, served_on: str, meal: int) -> list[dict]:
        ...


    @abstractmethod
    def add_line_report(self, location_id: int, band: str | None,
                        user_id: str, expires_at: str) -> None:
        ...

    @abstractmethod
    def latest_line_report(self, location_id: int) -> dict | None:
        ...

    @abstractmethod
    def line_report_history(self, location_id: int) -> list[dict]:
        ...


    @abstractmethod
    def attendance_baseline(self, location_id: int, sql_dow: str, meal: int,
                            before: str) -> tuple[float | None, int]:
        ...

    @abstractmethod
    def rated_extremes(self, location_id: int, limit: int) -> dict:
        ...

    @abstractmethod
    def top_wasted(self, location_id: int, since: str, limit: int) -> list[dict]:
        ...

    @abstractmethod
    def feedback_blocked(self, user_id: str) -> bool:
        ...

    @abstractmethod
    def set_feedback_block(self, note_id: int, user_id: str, blocked_by: str,
                           reason: str | None) -> None:
        ...

    @abstractmethod
    def clear_feedback_block(self, note_id: int, unblocked_by: str) -> bool:
        ...


    @abstractmethod
    def grill_station(self, location_id: int, default_cap: int) -> dict:
        ...

    @abstractmethod
    def set_grill_station(self, location_id: int, default_cap: int,
                          state: str | None = None,
                          app_cap: int | None = None) -> dict:
        ...

    @abstractmethod
    def grill_open_orders(self, location_id: int) -> list[dict]:
        ...

    @abstractmethod
    def grill_poll(self, location_id: int, default_cap: int) -> tuple[dict, list[dict]]:
        ...

    @abstractmethod
    def open_app_count(self, location_id: int) -> int:
        ...

    @abstractmethod
    def user_open_grill_order(self, user_id: str) -> dict | None:
        ...

    @abstractmethod
    def place_grill_order(self, location_id: int, user_id: str, main_id: int,
                          main_name: str, condiments_json: str,
                          pickup_name: str) -> dict:
        ...

    @abstractmethod
    def get_grill_order(self, order_id: int) -> dict | None: ...

    @abstractmethod
    def advance_grill_order(self, order_id: int, to: str) -> dict | None:
        ...

    @abstractmethod
    def cancel_grill_order(self, order_id: int, reason: str,
                           by_staff: bool) -> dict | None:
        ...

    @abstractmethod
    def grill_cook_estimate_s(self, location_id: int, default_s: int) -> int:
        ...

    @abstractmethod
    def grill_orders_containing(self, location_id: int, recipe_id: int) -> list[dict]:
        ...


    @abstractmethod
    def set_dish_intent(self, user_id: str, recipe_id: int, location_id: int,
                        intent: str | None) -> None:
        ...

    @abstractmethod
    def dish_intents_for(self, user_id: str, recipe_ids: list[int],
                         location_id: int, since: str) -> dict[int, str]:
        ...

    @abstractmethod
    def intent_counts(self, location_id: int, recipe_ids: list[int],
                      since: str) -> dict[int, dict]:
        ...

    @abstractmethod
    def has_rating(self, user_id: str, recipe_id: int,
                   location_id: int) -> bool:
        ...

    @abstractmethod
    def user_ratings_for(self, user_id: str, recipe_ids: list[int],
                         location_id: int) -> dict[int, int]:
        ...

    @abstractmethod
    def queue_picks(self, location_id: int) -> list[dict]:
        ...

    @abstractmethod
    def add_queue_pick(self, location_id: int, recipe_id: int,
                       user_id: str) -> None:
        ...

    @abstractmethod
    def remove_queue_pick(self, location_id: int, recipe_id: int) -> bool:
        ...


    @abstractmethod
    def attendance_yes_users(self, location_id: int, served_on: str,
                             meal: int) -> list[str]:
        ...

    @abstractmethod
    def grill_order_users(self, location_id: int, since: str) -> set[str]:
        ...

    @abstractmethod
    def queue_vetoes(self, location_id: int) -> set[int]:
        ...

    @abstractmethod
    def add_queue_veto(self, location_id: int, recipe_id: int,
                       user_id: str) -> None:
        ...

    @abstractmethod
    def throttle_unlock(self, ip: str, window_s: int) -> int:
        ...

    @abstractmethod
    def delete_push_sub_for_user(self, endpoint: str, user_sub: str) -> None:
        ...
