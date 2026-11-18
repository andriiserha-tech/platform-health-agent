"""Collector protocol and small helpers shared by collectors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from health_agent.models import CollectorResult


class Collector(Protocol):
    name: str

    def collect(self, now: datetime) -> CollectorResult:
        """Gather health data. `now` is passed in so results are deterministic and testable."""
        ...


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC; convert aware ones to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def from_epoch_ms(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, tz=UTC) if value else None


def to_epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def enum_str(value: Any) -> str | None:
    """SDKs return enums (or plain strings in older versions); normalise to str."""
    if value is None:
        return None
    return str(getattr(value, "value", value))
