"""Read-only context collection for shadow forward testing."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from meta_trader_ai.regime import classify_regime

FRED_REAL_YIELD_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
BLS_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BLS_MONTH_URL = "https://www.bls.gov/schedule/{year}/{month:02d}_sched_list.htm"
NEW_YORK = ZoneInfo("America/New_York")

FOMC_FINAL_DATES = (
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
)


@dataclass(frozen=True, slots=True)
class MiniCandle:
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class TimeframeFeatures:
    trend: str = "UNAVAILABLE"
    volatility: str = "UNAVAILABLE"
    efficiency_ratio: float | None = None
    net_move_atr: float | None = None
    volatility_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    name: str
    source: str
    impact: str
    time_utc: datetime
    timing_quality: str


@dataclass(frozen=True, slots=True)
class MarketContextFeatures:
    h1_trend: str = "UNAVAILABLE"
    h1_volatility: str = "UNAVAILABLE"
    h1_efficiency_ratio: float | None = None
    h1_net_move_atr: float | None = None
    h1_volatility_ratio: float | None = None
    h4_trend: str = "UNAVAILABLE"
    h4_volatility: str = "UNAVAILABLE"
    h4_efficiency_ratio: float | None = None
    h4_net_move_atr: float | None = None
    h4_volatility_ratio: float | None = None
    real_yield_10y: float | None = None
    real_yield_change_bp: float | None = None
    real_yield_date: str = ""
    next_event_name: str = ""
    next_event_source: str = ""
    next_event_impact: str = ""
    next_event_utc: str = ""
    minutes_to_event: float | None = None
    event_timing_quality: str = ""
    context_errors: str = ""


class MarketContextError(RuntimeError):
    """Raised when local MT5 context is missing, malformed, or stale."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_iso(value: str) -> datetime:
    return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _timeframe_features(payload: dict[str, object], key: str) -> TimeframeFeatures:
    raw = payload.get(key)
    if not isinstance(raw, dict):
        return TimeframeFeatures()
    highs, lows, closes = raw.get("highs", []), raw.get("lows", []), raw.get("closes", [])
    if not isinstance(highs, list) or not isinstance(lows, list) or not isinstance(closes, list):
        return TimeframeFeatures()
    if len(closes) < 65 or len(highs) != len(closes) or len(lows) != len(closes):
        return TimeframeFeatures()
    regime = classify_regime([
        MiniCandle(high=float(high), low=float(low), close=float(close))
        for high, low, close in zip(highs, lows, closes)
    ])
    return TimeframeFeatures(
        trend=regime.trend.value,
        volatility=regime.volatility.value,
        efficiency_ratio=regime.efficiency_ratio,
        net_move_atr=regime.net_move_atr,
        volatility_ratio=regime.volatility_ratio,
    )


def load_mt5_context(path: Path, *, symbol: str, max_age_seconds: int = 90, now: datetime | None = None) -> tuple[TimeframeFeatures, TimeframeFeatures]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketContextError(f"MT5 context unavailable: {exc}") from exc
    if str(payload.get("symbol", "")) != symbol:
        raise MarketContextError(f"MT5 context symbol mismatch: {payload.get('symbol')} != {symbol}")
    try:
        generated_at = _parse_iso(str(payload["generated_at"]))
    except (KeyError, ValueError) as exc:
        raise MarketContextError("MT5 context generated_at is invalid") from exc
    age = (_aware_utc(now or datetime.now(UTC)) - generated_at).total_seconds()
    if age > max_age_seconds:
        raise MarketContextError(f"MT5 context is stale ({age:.1f}s old)")
    h1, h4 = _timeframe_features(payload, "h1"), _timeframe_features(payload, "h4")
    if h1.trend == "UNAVAILABLE" or h4.trend == "UNAVAILABLE":
        raise MarketContextError("MT5 H1/H4 context arrays are incomplete")
    return h1, h4


def _unfold_ics(text: str) -> list[str]:
    result: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and result:
            result[-1] += raw[1:]
        else:
            result.append(raw)
    return result


def _parse_ics_datetime(left: str, raw_value: str) -> datetime | None:
    params: dict[str, str] = {}
    for item in left.split(";")[1:]:
        if "=" in item:
            key, value = item.split("=", 1)
            params[key.upper()] = value
    value = raw_value.strip()
    if len(value) == 8 and value.isdigit():
        return None
    try:
        if value.endswith("Z"):
            for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%MZ"):
                try:
                    return datetime.strptime(value, fmt).replace(tzinfo=UTC)
                except ValueError:
                    pass
            return None
        parsed = None
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            return None
        tz_name = params.get("TZID", "America/New_York").strip('"')
        return parsed.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
    except (ValueError, KeyError):
        return None


def _impact_for_bls(name: str) -> str | None:
    lowered = name.casefold()
    if any(item in lowered for item in ("employment situation", "consumer price index", "producer price index")):
        return "HIGH"
    if any(item in lowered for item in ("job openings and labor turnover", "employment cost index")):
        return "MEDIUM"
    return None


def parse_bls_ics(text: str) -> list[EconomicEvent]:
    events: list[EconomicEvent] = []
    current: dict[str, tuple[str, str]] | None = None
    for line in _unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                summary_item, start_item = current.get("SUMMARY"), current.get("DTSTART")
                if summary_item and start_item:
                    name = summary_item[1].replace("\\,", ",").replace("\\n", " ").strip()
                    impact, event_time = _impact_for_bls(name), _parse_ics_datetime(start_item[0], start_item[1])
                    if impact and event_time is not None:
                        events.append(EconomicEvent(name, "BLS", impact, event_time, "OFFICIAL_CALENDAR"))
            current = None
            continue
        if current is None or ":" not in line:
            continue
        left, value = line.split(":", 1)
        key = left.split(";", 1)[0].upper()
        if key in {"SUMMARY", "DTSTART"}:
            current[key] = (left, value)
    return sorted(events, key=lambda item: item.time_utc)


class _BlsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag.lower() in {"td", "th"}:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self.in_row and self.in_cell and lowered in {"td", "th"}:
            self.row.append(" ".join("".join(self.cell_parts).split()))
            self.in_cell = False
            self.cell_parts = []
        elif self.in_row and lowered == "tr":
            if self.row:
                self.rows.append(self.row)
            self.in_row = False
            self.row = []


def parse_bls_schedule_html(text: str) -> list[EconomicEvent]:
    """Parse the official BLS monthly List View table as an ICS fallback."""
    parser = _BlsTableParser()
    parser.feed(text)
    events: list[EconomicEvent] = []
    for row in parser.rows:
        if len(row) < 3 or row[0].casefold() == "date":
            continue
        raw_date, raw_time = row[0].strip(), row[1].strip()
        name = " ".join(row[2:]).strip()
        impact = _impact_for_bls(name)
        if not impact or not raw_time:
            continue
        try:
            day = datetime.strptime(raw_date, "%A, %B %d, %Y").date()
            release_time = datetime.strptime(raw_time, "%I:%M %p").time()
        except ValueError:
            continue
        event_time = datetime.combine(day, release_time, tzinfo=NEW_YORK).astimezone(UTC)
        events.append(EconomicEvent(name, "BLS", impact, event_time, "OFFICIAL_HTML"))
    return sorted(events, key=lambda item: item.time_utc)


def _month_sequence(current: datetime, count: int = 2) -> list[tuple[int, int]]:
    year, month = current.year, current.month
    result: list[tuple[int, int]] = []
    for _ in range(count):
        result.append((year, month))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return result


def fomc_events() -> list[EconomicEvent]:
    return [
        EconomicEvent(
            "FOMC policy decision", "Federal Reserve", "HIGH",
            datetime.combine(day, time(14, 0), tzinfo=NEW_YORK).astimezone(UTC),
            "STANDARD_14_ET",
        )
        for day in FOMC_FINAL_DATES
    ]


def parse_fred_real_yield(text: str) -> tuple[float, float | None, str]:
    values: list[tuple[str, float]] = []
    for row in csv.DictReader(io.StringIO(text)):
        raw = str(row.get("DFII10", "")).strip()
        day = str(row.get("DATE") or row.get("observation_date") or "").strip()
        if not raw or raw == "." or not day:
            continue
        try:
            values.append((day, float(raw)))
        except ValueError:
            pass
    if not values:
        raise ValueError("FRED DFII10 response contains no numeric observations")
    latest_day, latest_value = values[-1]
    change_bp = (latest_value - values[-2][1]) * 100.0 if len(values) >= 2 else None
    return latest_value, change_bp, latest_day


def _event_to_dict(event: EconomicEvent) -> dict[str, object]:
    result = asdict(event)
    result["time_utc"] = event.time_utc.isoformat()
    return result


def _event_from_dict(payload: dict[str, object]) -> EconomicEvent:
    return EconomicEvent(
        str(payload["name"]), str(payload["source"]), str(payload["impact"]),
        _parse_iso(str(payload["time_utc"])), str(payload["timing_quality"]),
    )


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/151 Safari/537.36 MetaTraderAIAssistant/0.3"
        ),
        "Accept": "text/html,application/xhtml+xml,text/calendar,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Referer": "https://www.bls.gov/schedule/",
    }


class MarketContextCollector:
    """Collect context without influencing trade decisions."""

    def __init__(self, *, mt5_context_path: Path, cache_path: Path = Path("data/market_context_cache.json"), mt5_max_age_seconds: int = 90, external_refresh_minutes: int = 60, timeout_seconds: float = 8.0) -> None:
        self.mt5_context_path = mt5_context_path
        self.cache_path = cache_path
        self.mt5_max_age_seconds = mt5_max_age_seconds
        self.external_refresh = timedelta(minutes=max(5, external_refresh_minutes))
        self.timeout_seconds = max(2.0, timeout_seconds)
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, object]:
        if not self.cache_path.exists():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def _cache_fresh(self, key: str, now: datetime) -> bool:
        raw = self._cache.get(f"{key}_fetched_at")
        if not raw:
            return False
        try:
            fetched = _parse_iso(str(raw))
        except ValueError:
            return False
        return now - fetched < self.external_refresh

    def _refresh_real_yield(self, now: datetime) -> None:
        if self._cache_fresh("real_yield", now):
            return
        response = httpx.get(FRED_REAL_YIELD_URL, timeout=self.timeout_seconds, follow_redirects=True, headers=_request_headers())
        response.raise_for_status()
        value, change_bp, day = parse_fred_real_yield(response.text)
        self._cache["real_yield"] = {"value": value, "change_bp": change_bp, "date": day}
        self._cache["real_yield_fetched_at"] = now.isoformat()
        self._save_cache()

    def _fetch_bls_html_fallback(self, now: datetime) -> list[EconomicEvent]:
        events: list[EconomicEvent] = []
        errors: list[str] = []
        for year, month in _month_sequence(now, 2):
            url = BLS_MONTH_URL.format(year=year, month=month)
            try:
                response = httpx.get(url, timeout=self.timeout_seconds, follow_redirects=True, headers=_request_headers())
                response.raise_for_status()
                events.extend(parse_bls_schedule_html(response.text))
            except httpx.HTTPError as exc:
                errors.append(f"{year}-{month:02d}: {exc}")
        unique = {(event.name, event.time_utc): event for event in events}
        result = sorted(unique.values(), key=lambda item: item.time_utc)
        if not result:
            detail = " | ".join(errors) if errors else "no relevant events parsed"
            raise MarketContextError(f"BLS HTML fallback failed: {detail}")
        return result

    def _refresh_bls(self, now: datetime) -> None:
        if self._cache_fresh("bls", now):
            return
        source = "ICS"
        try:
            response = httpx.get(BLS_CALENDAR_URL, timeout=self.timeout_seconds, follow_redirects=True, headers=_request_headers())
            response.raise_for_status()
            events = parse_bls_ics(response.text)
            if not events:
                raise MarketContextError("BLS ICS contained no relevant timed releases")
        except (httpx.HTTPError, MarketContextError):
            events = self._fetch_bls_html_fallback(now)
            source = "HTML_FALLBACK"
        self._cache["bls_events"] = [_event_to_dict(event) for event in events]
        self._cache["bls_source"] = source
        self._cache["bls_fetched_at"] = now.isoformat()
        self._save_cache()

    def _cached_events(self) -> list[EconomicEvent]:
        result = fomc_events()
        raw = self._cache.get("bls_events", [])
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    try:
                        result.append(_event_from_dict(item))
                    except (KeyError, ValueError, TypeError):
                        pass
        return sorted(result, key=lambda item: item.time_utc)

    def collect(self, *, symbol: str, now: datetime | None = None) -> MarketContextFeatures:
        current = _aware_utc(now or datetime.now(UTC))
        errors: list[str] = []
        h1, h4 = TimeframeFeatures(), TimeframeFeatures()
        try:
            h1, h4 = load_mt5_context(self.mt5_context_path, symbol=symbol, max_age_seconds=self.mt5_max_age_seconds, now=current)
        except MarketContextError as exc:
            errors.append(str(exc))
        try:
            self._refresh_real_yield(current)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            errors.append(f"FRED: {exc}")
        try:
            self._refresh_bls(current)
        except (httpx.HTTPError, ValueError, OSError, MarketContextError) as exc:
            errors.append(f"BLS: {exc}")

        real_yield = self._cache.get("real_yield", {})
        if not isinstance(real_yield, dict):
            real_yield = {}
        future_events = [event for event in self._cached_events() if event.time_utc >= current]
        next_event = future_events[0] if future_events else None
        minutes_to_event = ((next_event.time_utc - current).total_seconds() / 60.0) if next_event else None

        return MarketContextFeatures(
            h1_trend=h1.trend, h1_volatility=h1.volatility,
            h1_efficiency_ratio=h1.efficiency_ratio, h1_net_move_atr=h1.net_move_atr,
            h1_volatility_ratio=h1.volatility_ratio,
            h4_trend=h4.trend, h4_volatility=h4.volatility,
            h4_efficiency_ratio=h4.efficiency_ratio, h4_net_move_atr=h4.net_move_atr,
            h4_volatility_ratio=h4.volatility_ratio,
            real_yield_10y=float(real_yield["value"]) if real_yield.get("value") is not None else None,
            real_yield_change_bp=float(real_yield["change_bp"]) if real_yield.get("change_bp") is not None else None,
            real_yield_date=str(real_yield.get("date", "")),
            next_event_name=next_event.name if next_event else "",
            next_event_source=next_event.source if next_event else "",
            next_event_impact=next_event.impact if next_event else "",
            next_event_utc=next_event.time_utc.isoformat() if next_event else "",
            minutes_to_event=minutes_to_event,
            event_timing_quality=next_event.timing_quality if next_event else "",
            context_errors=" | ".join(errors),
        )
