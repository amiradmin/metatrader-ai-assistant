"""Additional read-only research context for XAUUSD shadow testing.

Nothing in this module can create, block, or modify a broker order.  It only
collects external features for later forward-test analysis:
- high-impact USD events from the Forex Factory weekly JSON export
- CFTC COMEX Gold disaggregated Managed Money positioning
- ICE US Dollar Index (DXY) daily context via Yahoo's chart feed, with an
  explicitly-labelled FRED broad-dollar proxy fallback

All feeds are cached locally and failures are returned as warnings rather than
interrupting the frozen shadow strategy.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx


FF_CALENDAR_URLS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
)
CFTC_DISAGG_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
YAHOO_DXY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=1mo&interval=1d"
FRED_USD_PROXY_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS"


@dataclass(frozen=True, slots=True)
class ForexFactoryEvent:
    name: str
    impact: str
    time_utc: datetime
    forecast: str = ""
    previous: str = ""


@dataclass(frozen=True, slots=True)
class CotGoldPositioning:
    report_date: str
    managed_money_long: int
    managed_money_short: int
    managed_money_net: int
    managed_money_net_change: int | None


@dataclass(frozen=True, slots=True)
class DollarIndexFeatures:
    value: float
    change_1d_pct: float | None
    change_5d_pct: float | None
    trend_5d: str
    observation_date: str
    source: str
    is_proxy: bool


@dataclass(frozen=True, slots=True)
class ResearchContextFeatures:
    ff_next_event_name: str = ""
    ff_next_event_impact: str = ""
    ff_next_event_utc: str = ""
    ff_minutes_to_event: float | None = None
    ff_forecast: str = ""
    ff_previous: str = ""
    cot_gold_report_date: str = ""
    cot_mm_long: int | None = None
    cot_mm_short: int | None = None
    cot_mm_net: int | None = None
    cot_mm_net_change: int | None = None
    dxy_value: float | None = None
    dxy_change_1d_pct: float | None = None
    dxy_change_5d_pct: float | None = None
    dxy_trend_5d: str = "UNAVAILABLE"
    dxy_observation_date: str = ""
    dxy_source: str = ""
    dxy_is_proxy: bool = False
    research_errors: str = ""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_iso(value: str) -> datetime:
    return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "MetaTraderAIAssistant/0.3 (+read-only research; Ubuntu)",
        "Accept": "application/json,text/plain,text/csv,*/*;q=0.8",
    }


def parse_forex_factory_json(text: str) -> list[ForexFactoryEvent]:
    """Parse high-impact USD events from the public weekly FF JSON export."""
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError("Forex Factory payload is not a list")

    events: list[ForexFactoryEvent] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("country", "")).upper() != "USD":
            continue
        impact = str(item.get("impact", "")).strip()
        if impact.casefold() != "high":
            continue
        raw_date = str(item.get("date", "")).strip()
        if not raw_date:
            continue
        try:
            event_time = _parse_iso(raw_date)
        except ValueError:
            continue
        events.append(
            ForexFactoryEvent(
                name=str(item.get("title", "")).strip(),
                impact="HIGH",
                time_utc=event_time,
                forecast=str(item.get("forecast", "") or "").strip(),
                previous=str(item.get("previous", "") or "").strip(),
            )
        )
    return sorted(events, key=lambda event: event.time_utc)


def parse_cftc_gold_disaggregated(text: str) -> CotGoldPositioning:
    """Extract COMEX Gold Managed Money fields from CFTC futures-only CSV.

    The current CFTC comma-delimited disaggregated layout uses 1-based fields:
    3 report date, 14 Managed Money long, 15 short, 62 long change, 63 short
    change.  The parser intentionally matches the main 100-troy-ounce GOLD
    contract and excludes MICRO GOLD.
    """
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 63:
            continue
        market = row[0].strip()
        if market != "GOLD - COMMODITY EXCHANGE INC.":
            continue
        try:
            long_value = int(row[13].strip())
            short_value = int(row[14].strip())
            long_change = int(row[61].strip())
            short_change = int(row[62].strip())
        except ValueError as exc:
            raise ValueError("CFTC Gold row has invalid Managed Money fields") from exc
        return CotGoldPositioning(
            report_date=row[2].strip(),
            managed_money_long=long_value,
            managed_money_short=short_value,
            managed_money_net=long_value - short_value,
            managed_money_net_change=long_change - short_change,
        )
    raise ValueError("CFTC COMEX Gold row not found")


def _series_features(
    values: list[tuple[str, float]],
    *,
    source: str,
    is_proxy: bool,
) -> DollarIndexFeatures:
    if not values:
        raise ValueError("Dollar-index series contains no observations")
    date_value, latest = values[-1]
    change_1d = None
    if len(values) >= 2 and abs(values[-2][1]) > 1e-12:
        change_1d = (latest / values[-2][1] - 1.0) * 100.0
    change_5d = None
    if len(values) >= 6 and abs(values[-6][1]) > 1e-12:
        change_5d = (latest / values[-6][1] - 1.0) * 100.0
    elif len(values) >= 2 and abs(values[0][1]) > 1e-12:
        change_5d = (latest / values[0][1] - 1.0) * 100.0

    if change_5d is None:
        trend = "UNAVAILABLE"
    elif change_5d > 0.05:
        trend = "UP"
    elif change_5d < -0.05:
        trend = "DOWN"
    else:
        trend = "FLAT"

    return DollarIndexFeatures(
        value=latest,
        change_1d_pct=change_1d,
        change_5d_pct=change_5d,
        trend_5d=trend,
        observation_date=date_value,
        source=source,
        is_proxy=is_proxy,
    )


def parse_yahoo_dxy_json(text: str) -> DollarIndexFeatures:
    """Parse daily DX-Y.NYB closes from Yahoo's chart JSON response."""
    payload: dict[str, Any] = json.loads(text)
    result_list = ((payload.get("chart") or {}).get("result") or [])
    if not result_list:
        raise ValueError("Yahoo DXY response contains no chart result")
    result = result_list[0]
    timestamps = result.get("timestamp") or []
    quote_list = ((result.get("indicators") or {}).get("quote") or [])
    if not quote_list:
        raise ValueError("Yahoo DXY response contains no quote series")
    closes = quote_list[0].get("close") or []

    values: list[tuple[str, float]] = []
    for stamp, close in zip(timestamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(int(stamp), tz=UTC).date().isoformat()
        values.append((day, float(close)))
    return _series_features(values, source="Yahoo DX-Y.NYB", is_proxy=False)


def parse_fred_usd_proxy_csv(text: str) -> DollarIndexFeatures:
    """Parse FRED DTWEXBGS as a labelled fallback, not as literal DXY."""
    values: list[tuple[str, float]] = []
    for row in csv.DictReader(io.StringIO(text)):
        raw = str(row.get("DTWEXBGS", "")).strip()
        day = str(row.get("DATE") or row.get("observation_date") or "").strip()
        if not raw or raw == "." or not day:
            continue
        try:
            values.append((day, float(raw)))
        except ValueError:
            continue
    return _series_features(values, source="FRED DTWEXBGS broad-dollar proxy", is_proxy=True)


def _ff_event_to_dict(event: ForexFactoryEvent) -> dict[str, object]:
    result = asdict(event)
    result["time_utc"] = event.time_utc.isoformat()
    return result


def _ff_event_from_dict(item: dict[str, object]) -> ForexFactoryEvent:
    return ForexFactoryEvent(
        name=str(item["name"]),
        impact=str(item["impact"]),
        time_utc=_parse_iso(str(item["time_utc"])),
        forecast=str(item.get("forecast", "")),
        previous=str(item.get("previous", "")),
    )


class ResearchContextCollector:
    """Cached observer-only collector for FF, CFTC and dollar-index features."""

    def __init__(
        self,
        *,
        cache_path: Path = Path("data/research_context_cache.json"),
        timeout_seconds: float = 8.0,
    ) -> None:
        self.cache_path = cache_path
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

    def _fresh(self, key: str, now: datetime, minutes: int) -> bool:
        raw = self._cache.get(f"{key}_fetched_at")
        if not raw:
            return False
        try:
            fetched = _parse_iso(str(raw))
        except ValueError:
            return False
        return now - fetched < timedelta(minutes=minutes)

    def _refresh_ff(self, now: datetime) -> None:
        if self._fresh("ff", now, 60):
            return
        errors: list[str] = []
        for url in FF_CALENDAR_URLS:
            try:
                response = httpx.get(
                    url,
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                    headers=_headers(),
                )
                response.raise_for_status()
                events = parse_forex_factory_json(response.text)
                self._cache["ff_events"] = [_ff_event_to_dict(event) for event in events]
                self._cache["ff_source"] = url
                self._cache["ff_fetched_at"] = now.isoformat()
                self._save_cache()
                return
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{url}: {exc}")
        raise RuntimeError("Forex Factory unavailable: " + " | ".join(errors))

    def _refresh_cftc(self, now: datetime) -> None:
        if self._fresh("cftc", now, 360):
            return
        response = httpx.get(
            CFTC_DISAGG_URL,
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers=_headers(),
        )
        response.raise_for_status()
        positioning = parse_cftc_gold_disaggregated(response.text)
        self._cache["cftc_gold"] = asdict(positioning)
        self._cache["cftc_fetched_at"] = now.isoformat()
        self._save_cache()

    def _refresh_dxy(self, now: datetime) -> None:
        if self._fresh("dxy", now, 60):
            return
        yahoo_error = ""
        try:
            response = httpx.get(
                YAHOO_DXY_URL,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers=_headers(),
            )
            response.raise_for_status()
            features = parse_yahoo_dxy_json(response.text)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            yahoo_error = str(exc)
            response = httpx.get(
                FRED_USD_PROXY_URL,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers=_headers(),
            )
            response.raise_for_status()
            features = parse_fred_usd_proxy_csv(response.text)

        self._cache["dxy"] = asdict(features)
        self._cache["dxy_yahoo_error"] = yahoo_error
        self._cache["dxy_fetched_at"] = now.isoformat()
        self._save_cache()

    def _cached_ff_events(self) -> list[ForexFactoryEvent]:
        result: list[ForexFactoryEvent] = []
        raw = self._cache.get("ff_events", [])
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    result.append(_ff_event_from_dict(item))
                except (KeyError, TypeError, ValueError):
                    continue
        return sorted(result, key=lambda event: event.time_utc)

    def collect(self, *, now: datetime | None = None) -> ResearchContextFeatures:
        current = _aware_utc(now or datetime.now(UTC))
        errors: list[str] = []
        try:
            self._refresh_ff(current)
        except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
            errors.append(f"FF: {exc}")
        try:
            self._refresh_cftc(current)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            errors.append(f"CFTC: {exc}")
        try:
            self._refresh_dxy(current)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            errors.append(f"DXY: {exc}")

        future_events = [event for event in self._cached_ff_events() if event.time_utc >= current]
        next_event = future_events[0] if future_events else None
        minutes = (
            (next_event.time_utc - current).total_seconds() / 60.0
            if next_event is not None
            else None
        )

        cot = self._cache.get("cftc_gold", {})
        if not isinstance(cot, dict):
            cot = {}
        dxy = self._cache.get("dxy", {})
        if not isinstance(dxy, dict):
            dxy = {}

        return ResearchContextFeatures(
            ff_next_event_name=next_event.name if next_event else "",
            ff_next_event_impact=next_event.impact if next_event else "",
            ff_next_event_utc=next_event.time_utc.isoformat() if next_event else "",
            ff_minutes_to_event=minutes,
            ff_forecast=next_event.forecast if next_event else "",
            ff_previous=next_event.previous if next_event else "",
            cot_gold_report_date=str(cot.get("report_date", "")),
            cot_mm_long=int(cot["managed_money_long"]) if cot.get("managed_money_long") is not None else None,
            cot_mm_short=int(cot["managed_money_short"]) if cot.get("managed_money_short") is not None else None,
            cot_mm_net=int(cot["managed_money_net"]) if cot.get("managed_money_net") is not None else None,
            cot_mm_net_change=int(cot["managed_money_net_change"]) if cot.get("managed_money_net_change") is not None else None,
            dxy_value=float(dxy["value"]) if dxy.get("value") is not None else None,
            dxy_change_1d_pct=float(dxy["change_1d_pct"]) if dxy.get("change_1d_pct") is not None else None,
            dxy_change_5d_pct=float(dxy["change_5d_pct"]) if dxy.get("change_5d_pct") is not None else None,
            dxy_trend_5d=str(dxy.get("trend_5d", "UNAVAILABLE")),
            dxy_observation_date=str(dxy.get("observation_date", "")),
            dxy_source=str(dxy.get("source", "")),
            dxy_is_proxy=bool(dxy.get("is_proxy", False)),
            research_errors=" | ".join(errors),
        )
