"""Economic-calendar ingestion for scheduled macro-event risk.

Forex Factory exposes a weekly JSON export from its calendar page.  This module
uses that export only as a scheduled-event risk gate: it never creates trade
direction.  High-impact events can block new entries around the release window;
medium-impact events can reduce confidence through the existing news-risk layer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from meta_trader_ai.models import NewsItem

logger = logging.getLogger(__name__)

FOREX_FACTORY_CALENDAR_PAGE = "https://www.forexfactory.com/calendar?day=today"


class EconomicCalendarError(RuntimeError):
    """Raised when the configured economic-calendar feed cannot be trusted."""


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """One scheduled economic event from the calendar export."""

    title: str
    currency: str
    scheduled_at: datetime
    impact: str
    forecast: str = ""
    previous: str = ""


@dataclass(slots=True)
class _CalendarCache:
    events: list[CalendarEvent]
    fetched_at: datetime


_cache: dict[str, _CalendarCache] = {}
_cache_lock = asyncio.Lock()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing calendar date")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return _aware_utc(parsed)


def parse_calendar_payload(payload: object) -> list[CalendarEvent]:
    """Parse Forex Factory's weekly JSON export defensively."""
    if not isinstance(payload, list):
        raise EconomicCalendarError("economic calendar payload is not a list")

    events: list[CalendarEvent] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title", "")).strip()
        currency = str(raw.get("country", "")).strip().upper()
        impact = str(raw.get("impact", "")).strip().upper()
        if not title or not currency:
            continue
        try:
            scheduled_at = _parse_datetime(raw.get("date"))
        except (TypeError, ValueError):
            continue
        events.append(
            CalendarEvent(
                title=title,
                currency=currency,
                scheduled_at=scheduled_at,
                impact=impact,
                forecast=str(raw.get("forecast", "") or "").strip(),
                previous=str(raw.get("previous", "") or "").strip(),
            )
        )

    if not events:
        raise EconomicCalendarError("economic calendar contained no usable events")
    return events


async def fetch_calendar_events(
    url: str,
    *,
    cache_seconds: int = 300,
    stale_fallback_minutes: int = 180,
) -> list[CalendarEvent]:
    """Fetch and cache the weekly calendar export.

    A short cache avoids hitting the provider on every MT5 /hint poll.  If a
    refresh temporarily fails, a recent last-known-good calendar may be used.
    """
    now = datetime.now(UTC)
    cached = _cache.get(url)
    if cached and (now - cached.fetched_at).total_seconds() <= cache_seconds:
        return list(cached.events)

    async with _cache_lock:
        now = datetime.now(UTC)
        cached = _cache.get(url)
        if cached and (now - cached.fetched_at).total_seconds() <= cache_seconds:
            return list(cached.events)

        try:
            async with httpx.AsyncClient(
                timeout=12,
                headers={"User-Agent": "MT5-AI-Assistant/0.4"},
            ) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                events = parse_calendar_payload(response.json())
        except (httpx.HTTPError, ValueError, EconomicCalendarError) as exc:
            cached = _cache.get(url)
            if cached:
                age_minutes = (now - cached.fetched_at).total_seconds() / 60.0
                if age_minutes <= stale_fallback_minutes:
                    logger.warning(
                        "Economic calendar refresh failed; using %.1f-minute-old cache: %s",
                        age_minutes,
                        exc,
                    )
                    return list(cached.events)
            raise EconomicCalendarError(f"economic calendar unavailable: {exc}") from exc

        _cache[url] = _CalendarCache(events=list(events), fetched_at=now)
        return events


def _symbol_currencies(symbol: str) -> set[str]:
    normalized = "".join(character for character in symbol.upper() if character.isalpha())
    currencies: set[str] = set()
    if len(normalized) >= 3:
        currencies.add(normalized[:3])
    if len(normalized) >= 6:
        currencies.add(normalized[3:6])
    return currencies


def _inside_window(
    scheduled_at: datetime,
    *,
    now: datetime,
    before_minutes: int,
    after_minutes: int,
) -> bool:
    return (
        scheduled_at - timedelta(minutes=max(0, before_minutes))
        <= now
        <= scheduled_at + timedelta(minutes=max(0, after_minutes))
    )


def calendar_news_items(
    symbol: str,
    events: list[CalendarEvent],
    *,
    now: datetime | None = None,
    high_before_minutes: int = 30,
    high_after_minutes: int = 30,
    medium_before_minutes: int = 15,
    medium_after_minutes: int = 10,
) -> list[NewsItem]:
    """Convert active calendar windows into the existing NewsItem risk layer."""
    current = _aware_utc(now or datetime.now(UTC))
    currencies = _symbol_currencies(symbol)
    active: list[NewsItem] = []

    for event in events:
        if event.currency not in currencies and event.currency not in {"ALL", ""}:
            continue

        impact = event.impact.upper()
        impact_score = 0
        if impact == "HIGH" and _inside_window(
            event.scheduled_at,
            now=current,
            before_minutes=high_before_minutes,
            after_minutes=high_after_minutes,
        ):
            impact_score = 100
        elif impact in {"MEDIUM", "MED"} and _inside_window(
            event.scheduled_at,
            now=current,
            before_minutes=medium_before_minutes,
            after_minutes=medium_after_minutes,
        ):
            impact_score = 25

        if impact_score == 0:
            continue

        minutes_to_event = int(round((event.scheduled_at - current).total_seconds() / 60.0))
        timing = (
            f"in {minutes_to_event}m"
            if minutes_to_event >= 0
            else f"{abs(minutes_to_event)}m ago"
        )
        active.append(
            NewsItem(
                source="Forex Factory Calendar",
                title=(
                    f"{impact.title()} impact {event.currency}: {event.title} "
                    f"({timing})"
                ),
                url=FOREX_FACTORY_CALENDAR_PAGE,
                published_at=event.scheduled_at,
                currencies={event.currency} if event.currency != "ALL" else currencies,
                impact_score=impact_score,
            )
        )

    return sorted(active, key=lambda item: item.impact_score, reverse=True)


async def collect_calendar_news(
    symbol: str,
    url: str,
    *,
    cache_seconds: int = 300,
    stale_fallback_minutes: int = 180,
    high_before_minutes: int = 30,
    high_after_minutes: int = 30,
    medium_before_minutes: int = 15,
    medium_after_minutes: int = 10,
) -> list[NewsItem]:
    """Fetch the calendar and return only events active for the current symbol."""
    events = await fetch_calendar_events(
        url,
        cache_seconds=cache_seconds,
        stale_fallback_minutes=stale_fallback_minutes,
    )
    return calendar_news_items(
        symbol,
        events,
        high_before_minutes=high_before_minutes,
        high_after_minutes=high_after_minutes,
        medium_before_minutes=medium_before_minutes,
        medium_after_minutes=medium_after_minutes,
    )


def fail_closed_guard(symbol: str, reason: str) -> NewsItem:
    """Return a synthetic HIGH-risk item when calendar availability is mandatory."""
    currencies = _symbol_currencies(symbol)
    return NewsItem(
        source="Economic Calendar Guard",
        title=f"Calendar unavailable; new entries blocked ({reason})",
        url=FOREX_FACTORY_CALENDAR_PAGE,
        published_at=datetime.now(UTC),
        currencies=currencies,
        impact_score=100,
    )
