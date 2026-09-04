"""Resilient economic-calendar service with a persistent last-known-good cache."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from meta_trader_ai import economic_calendar as calendar_backend
from meta_trader_ai.economic_calendar import (
    CalendarEvent,
    EconomicCalendarError,
    calendar_news_items,
    fetch_calendar_events,
)
from meta_trader_ai.models import NewsItem

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _event_to_dict(event: CalendarEvent) -> dict[str, str]:
    return {
        "title": event.title,
        "currency": event.currency,
        "scheduled_at": _utc(event.scheduled_at).isoformat(),
        "impact": event.impact,
        "forecast": event.forecast,
        "previous": event.previous,
    }


def _event_from_dict(raw: object) -> CalendarEvent | None:
    if not isinstance(raw, dict):
        return None
    try:
        title = str(raw["title"]).strip()
        currency = str(raw["currency"]).strip().upper()
        impact = str(raw["impact"]).strip().upper()
        scheduled_at = datetime.fromisoformat(
            str(raw["scheduled_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not title or not currency or not impact:
        return None
    return CalendarEvent(
        title=title,
        currency=currency,
        scheduled_at=_utc(scheduled_at),
        impact=impact,
        forecast=str(raw.get("forecast", "") or "").strip(),
        previous=str(raw.get("previous", "") or "").strip(),
    )


def save_calendar_disk_cache(
    path: Path,
    events: list[CalendarEvent],
    *,
    fetched_at: datetime | None = None,
) -> None:
    """Atomically persist a last-known-good weekly calendar."""
    if not events:
        return
    timestamp = _utc(fetched_at or datetime.now(UTC))
    payload = {
        "fetched_at": timestamp.isoformat(),
        "events": [_event_to_dict(event) for event in events],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_calendar_disk_cache(
    path: Path,
    *,
    max_age_minutes: int,
    now: datetime | None = None,
) -> list[CalendarEvent]:
    """Load a recent cache only when it still covers the current calendar week."""
    current = _utc(now or datetime.now(UTC))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = _utc(
            datetime.fromisoformat(str(raw["fetched_at"]).replace("Z", "+00:00"))
        )
        raw_events = raw["events"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EconomicCalendarError(f"persistent calendar cache unavailable: {exc}") from exc

    age = current - fetched_at
    if age < timedelta(minutes=-5) or age > timedelta(minutes=max(1, max_age_minutes)):
        raise EconomicCalendarError(
            f"persistent calendar cache is stale ({age.total_seconds() / 60.0:.1f}m old)"
        )

    if not isinstance(raw_events, list):
        raise EconomicCalendarError("persistent calendar cache events are invalid")
    events = [event for item in raw_events if (event := _event_from_dict(item)) is not None]
    if not events:
        raise EconomicCalendarError("persistent calendar cache contains no usable events")

    first_event = min(event.scheduled_at for event in events)
    last_event = max(event.scheduled_at for event in events)
    coverage_start = first_event - timedelta(days=1)
    coverage_end = last_event + timedelta(days=1)
    if not coverage_start <= current <= coverage_end:
        raise EconomicCalendarError(
            "persistent calendar cache does not cover the current calendar week"
        )
    return events


def _memory_cache_timestamp(url: str) -> datetime | None:
    """Return the backend's actual last-good fetch time without extending freshness."""
    cached = calendar_backend._cache.get(url)  # package-private shared backend state
    return cached.fetched_at if cached is not None else None


async def collect_calendar_news_resilient(
    symbol: str,
    url: str,
    *,
    disk_cache_path: Path,
    disk_stale_minutes: int = 1440,
    cache_seconds: int = 900,
    stale_fallback_minutes: int = 180,
    request_timeout_seconds: float = 25.0,
    failure_cooldown_seconds: int = 300,
    max_attempts: int = 2,
    high_before_minutes: int = 30,
    high_after_minutes: int = 30,
    medium_before_minutes: int = 15,
    medium_after_minutes: int = 10,
) -> list[NewsItem]:
    """Use live calendar data when possible, otherwise a bounded on-disk cache.

    The persistent cache survives uvicorn/OS restarts. Its timestamp is copied
    from the backend's true last-good provider fetch, so repeated /hint requests
    cannot make old calendar data look newer than it really is. If neither live
    nor cached data is trustworthy, the caller keeps the fail-closed guard.
    """
    try:
        events = await fetch_calendar_events(
            url,
            cache_seconds=cache_seconds,
            stale_fallback_minutes=stale_fallback_minutes,
            request_timeout_seconds=request_timeout_seconds,
            failure_cooldown_seconds=failure_cooldown_seconds,
            max_attempts=max_attempts,
        )
    except EconomicCalendarError as live_exc:
        try:
            events = load_calendar_disk_cache(
                disk_cache_path,
                max_age_minutes=disk_stale_minutes,
            )
        except EconomicCalendarError as disk_exc:
            raise EconomicCalendarError(
                f"live calendar failed ({live_exc}); persistent cache failed ({disk_exc})"
            ) from live_exc
        logger.warning(
            "Economic calendar live refresh unavailable; using persistent disk cache: %s",
            live_exc,
        )
    else:
        try:
            save_calendar_disk_cache(
                disk_cache_path,
                events,
                fetched_at=_memory_cache_timestamp(url),
            )
        except OSError as exc:
            logger.warning("Could not persist economic calendar cache: %s", exc)

    return calendar_news_items(
        symbol,
        events,
        high_before_minutes=high_before_minutes,
        high_after_minutes=high_after_minutes,
        medium_before_minutes=medium_before_minutes,
        medium_after_minutes=medium_after_minutes,
    )
