from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time

from ..config import LOCAL_TZ


@dataclass(frozen=True)
class LineReading:
    location_id: int
    at: datetime
    entries_per_min: float
    wait_minutes: int
    busyness: float
    simulated: bool


class LineLengthProvider(ABC):

    @abstractmethod
    def current(self, location_id: int, now: datetime | None = None) -> LineReading: ...

    @abstractmethod
    def typical_day(self, location_id: int, on: date) -> list[tuple[time, float]]:
        ...


_WEEKDAY = [
    6, 9, 14, 18, 15, 9,
    5, 4,
    22, 46, 71, 88, 74, 41,
    16, 8, 6, 5,
    12, 34,
    68, 92, 100, 81, 44,
    18, 9, 6, 5,
]
_WEEKEND = [
    2, 3, 5, 7, 8, 9,
    14, 22,
    38, 62, 78, 84, 66, 38,
    14, 7, 5, 4,
    9, 22,
    48, 63, 66, 52, 28,
    12, 7, 5, 4,
]

_BUCKET_MINUTES = 30
_START_HOUR = 7


def _bucket_index(t: time) -> int:
    minutes = (t.hour - _START_HOUR) * 60 + t.minute
    return minutes // _BUCKET_MINUTES


class SimulatedLineProvider(LineLengthProvider):

    _SCALE = {30: 2.6, 9: 1.0, 5: 1.2, 7: 1.3, 8: 1.0, 14: 1.3, 15: 1.3, 16: 1.0, 38: 0.9}

    def _curve(self, on: date) -> list[int]:
        return _WEEKEND if on.weekday() >= 5 else _WEEKDAY

    def _jitter(self, location_id: int, on: date, idx: int) -> float:
        seed = (location_id * 733 + on.toordinal() * 97 + idx * 29) % 100
        return 0.85 + seed / 333.0

    def current(self, location_id: int, now: datetime | None = None) -> LineReading:
        now = now or datetime.now(LOCAL_TZ)
        curve = self._curve(now.date())
        idx = _bucket_index(now.time())

        if idx < 0 or idx >= len(curve):
            return LineReading(location_id, now, 0.0, 0, 0.0, True)

        peak = max(curve)
        raw = curve[idx] * self._jitter(location_id, now.date(), idx)
        busyness = max(0.0, min(1.0, raw / peak))
        scale = self._SCALE.get(location_id, 1.0)
        entries = round(busyness * 18.0 * scale, 1)

        wait = int(round(14 * busyness ** 2.2))

        return LineReading(
            location_id=location_id,
            at=now,
            entries_per_min=entries,
            wait_minutes=wait,
            busyness=round(busyness, 3),
            simulated=True,
        )

    def typical_day(self, location_id: int, on: date) -> list[tuple[time, float]]:
        curve = self._curve(on)
        peak = max(curve)
        out: list[tuple[time, float]] = []
        for idx, value in enumerate(curve):
            minutes = _START_HOUR * 60 + idx * _BUCKET_MINUTES
            slot = time(hour=minutes // 60, minute=minutes % 60)
            scaled = value * self._jitter(location_id, on, idx) / peak
            out.append((slot, round(max(0.0, min(1.0, scaled)), 3)))
        return out


class SwipeFeedLineProvider(LineLengthProvider):

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError(
            "Real swipe-feed line data requires a HUDS data-sharing agreement. "
            "The prototype runs SimulatedLineProvider; this class documents the "
            "integration point."
        )

    def current(self, location_id: int, now: datetime | None = None) -> LineReading:
        raise NotImplementedError

    def typical_day(self, location_id: int, on: date) -> list[tuple[time, float]]:
        raise NotImplementedError
