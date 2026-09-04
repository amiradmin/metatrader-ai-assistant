from datetime import UTC, datetime, timedelta

import pytest

from meta_trader_ai.calendar_service import (
    load_calendar_disk_cache,
    save_calendar_disk_cache,
)
from meta_trader_ai.economic_calendar import CalendarEvent, EconomicCalendarError


def _events(now: datetime) -> list[CalendarEvent]:
    return [
        CalendarEvent(
            title="Non-Farm Employment Change",
            currency="USD",
            scheduled_at=now + timedelta(hours=4),
            impact="HIGH",
            forecast="55K",
            previous="-23K",
        ),
        CalendarEvent(
            title="Unemployment Rate",
            currency="USD",
            scheduled_at=now + timedelta(hours=4),
            impact="HIGH",
            forecast="4.1%",
            previous="4.1%",
        ),
    ]


def test_persistent_calendar_cache_round_trip(tmp_path) -> None:
    now = datetime(2026, 9, 4, 4, 30, tzinfo=UTC)
    path = tmp_path / "calendar.json"
    save_calendar_disk_cache(path, _events(now), fetched_at=now)

    loaded = load_calendar_disk_cache(path, max_age_minutes=1440, now=now)

    assert len(loaded) == 2
    assert loaded[0].currency == "USD"
    assert loaded[0].impact == "HIGH"


def test_persistent_calendar_cache_rejects_stale_file(tmp_path) -> None:
    now = datetime(2026, 9, 4, 4, 30, tzinfo=UTC)
    path = tmp_path / "calendar.json"
    save_calendar_disk_cache(
        path,
        _events(now - timedelta(days=2)),
        fetched_at=now - timedelta(days=2),
    )

    with pytest.raises(EconomicCalendarError, match="stale"):
        load_calendar_disk_cache(path, max_age_minutes=1440, now=now)


def test_persistent_calendar_cache_rejects_wrong_week(tmp_path) -> None:
    now = datetime(2026, 9, 14, 4, 30, tzinfo=UTC)
    path = tmp_path / "calendar.json"
    old_week = datetime(2026, 9, 4, 4, 30, tzinfo=UTC)
    save_calendar_disk_cache(path, _events(old_week), fetched_at=now)

    with pytest.raises(EconomicCalendarError, match="current calendar week"):
        load_calendar_disk_cache(path, max_age_minutes=1440, now=now)
