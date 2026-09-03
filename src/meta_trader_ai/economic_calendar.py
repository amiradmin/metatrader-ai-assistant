"""Economic-calendar ingestion for scheduled macro-event risk.

Forex Factory exposes a weekly JSON export from its calendar page. This module
uses that export only as a scheduled-event risk gate: it never creates trade
direction. High-impact events can block new entries around the release window;
medium-impact events can reduce confidence through the existing news-risk layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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


@dataclass(slots=True)
class _CalendarFailure:
    reason: str
    failed_at: datetime


_cache: dict[str, _CalendarCache] = {}
_failures: dict[str, _CalendarFailure] = {}
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


def _failure_reason(exc: Exception, url: str) -> str:
    """Return a useful error even for exceptions such as ReadTimeout with empty str()."""
    detail = str(exc).strip()
    name = type(exc).__name__
    if detail:
        return f"{name}: {detail} [{url}]"
    return f"{name} while requesting {url}"


def _trust_env_for_attempt(attempt: int) -> bool:
    """Use normal proxy/env routing first, then retry with a direct connection."""
    return attempt == 1


async def _fetch_with_curl(url: str, timeout_seconds: float) -> list[CalendarEvent]:
    """Use the host curl binary as a final network fallback.

    Some Linux/VPN combinations allow curl to reach the calendar while Python's
    httpx connection path times out. The subprocess is argv-only (no shell) and
    forces IPv4 because this failure is commonly caused by an unusable IPv6 route.
    """
    timeout = max(5, int(round(timeout_seconds)))
    try:
        process = await asyncio.create_subprocess_exec(
            "curl",
            "-4",
            "-fLsS",
            "--connect-timeout",
            str(min(10, timeout)),
            "--max-time",
            str(timeout),
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise EconomicCalendarError("curl fallback unavailable: curl not installed") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout + 2,
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise EconomicCalendarError("curl fallback timed out") from exc

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise EconomicCalendarError(
            f"curl fallback failed with exit {process.returncode}"
            + (f": {detail}" if detail else "")
        )

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EconomicCalendarError(f"curl fallback returned invalid JSON: {exc}") from exc
    return parse_calendar_payload(payload)


async def fetch_calendar_events(
    url: str,
    *,
    cache_seconds: int = 300,
    stale_fallback_minutes: int = 180,
    request_timeout_seconds: float = 25.0,
    failure_cooldown_seconds: int = 300,
    max_attempts: int = 2,
) -> list[CalendarEvent]:
    """Fetch and cache the weekly calendar export.

    httpx is tried first with environment/proxy routing, then without it. If both
    fail, a curl -4 subprocess is used because that path is known to work on some
    Ubuntu/VPN setups where httpx cannot connect. A recent last-known-good cache
    remains usable for a bounded period and the guard stays fail-closed if all
    refresh paths fail.
    """
    now = datetime.now(UTC)
    cached = _cache.get(url)
    if cached and (now - cached.fetched_at).total_seconds() <= cache_seconds:
        return list(cached.events)

    failure = _failures.get(url)
    if failure and (now - failure.failed_at).total_seconds() <= failure_cooldown_seconds:
        if cached:
            age_minutes = (now - cached.fetched_at).total_seconds() / 60.0
            if age_minutes <= stale_fallback_minutes:
                return list(cached.events)
        raise EconomicCalendarError(failure.reason)

    async with _cache_lock:
        now = datetime.now(UTC)
        cached = _cache.get(url)
        if cached and (now - cached.fetched_at).total_seconds() <= cache_seconds:
            return list(cached.events)

        failure = _failures.get(url)
        if failure and (now - failure.failed_at).total_seconds() <= failure_cooldown_seconds:
            if cached:
                age_minutes = (now - cached.fetched_at).total_seconds() / 60.0
                if age_minutes <= stale_fallback_minutes:
                    return list(cached.events)
            raise EconomicCalendarError(failure.reason)

        last_exc: Exception | None = None
        attempts = max(1, max_attempts)
        timeout = httpx.Timeout(
            max(1.0, float(request_timeout_seconds)),
            connect=min(10.0, max(1.0, float(request_timeout_seconds))),
        )

        for attempt in range(1, attempts + 1):
            trust_env = _trust_env_for_attempt(attempt)
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    trust_env=trust_env,
                    headers={
                        "User-Agent": "Mozilla/5.0 MT5-AI-Assistant/0.4",
                        "Accept": "application/json,text/plain,*/*",
                    },
                ) as client:
                    response = await client.get(url, follow_redirects=True)
                    response.raise_for_status()
                    events = parse_calendar_payload(response.json())

                _cache[url] = _CalendarCache(events=list(events), fetched_at=now)
                _failures.pop(url, None)
                if attempt > 1:
                    logger.info("Economic calendar connected on direct httpx fallback.")
                return events
            except (httpx.HTTPError, ValueError, EconomicCalendarError) as exc:
                last_exc = exc
                logger.warning(
                    "Economic calendar httpx attempt %d/%d failed (trust_env=%s): %s",
                    attempt,
                    attempts,
                    trust_env,
                    _failure_reason(exc, url),
                )
                if attempt < attempts:
                    await asyncio.sleep(0.75 * attempt)

        try:
            events = await _fetch_with_curl(url, request_timeout_seconds)
        except EconomicCalendarError as exc:
            last_exc = exc
            logger.warning("Economic calendar curl fallback failed: %s", exc)
        else:
            _cache[url] = _CalendarCache(events=list(events), fetched_at=now)
            _failures.pop(url, None)
            logger.info("Economic calendar refreshed via curl IPv4 fallback.")
            return events

        assert last_exc is not None
        reason = _failure_reason(last_exc, url)
        _failures[url] = _CalendarFailure(reason=reason, failed_at=now)

        cached = _cache.get(url)
        if cached:
            age_minutes = (now - cached.fetched_at).total_seconds() / 60.0
            if age_minutes <= stale_fallback_minutes:
                logger.warning(
                    "Economic calendar refresh failed; using %.1f-minute-old cache: %s",
                    age_minutes,
                    reason,
                )
                return list(cached.events)

        raise EconomicCalendarError(f"economic calendar unavailable: {reason}") from last_exc


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
    request_timeout_seconds: float = 25.0,
    failure_cooldown_seconds: int = 300,
    max_attempts: int = 2,
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
        request_timeout_seconds=request_timeout_seconds,
        failure_cooldown_seconds=failure_cooldown_seconds,
        max_attempts=max_attempts,
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
