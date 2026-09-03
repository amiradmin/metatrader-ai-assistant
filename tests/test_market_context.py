import json
from datetime import UTC, datetime

import pytest

from meta_trader_ai.market_context import (
    MarketContextError,
    fomc_events,
    load_mt5_context,
    parse_bls_ics,
    parse_bls_schedule_html,
    parse_fred_real_yield,
)


def test_parse_fred_real_yield_accepts_fredgraph_header() -> None:
    text = "observation_date,DFII10\n2026-08-24,2.38\n2026-08-25,2.32\n"
    value, change_bp, day = parse_fred_real_yield(text)
    assert value == pytest.approx(2.32)
    assert change_bp == pytest.approx(-6.0)
    assert day == "2026-08-25"


def test_parse_bls_ics_keeps_market_moving_release() -> None:
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20260904T083000
SUMMARY:Employment Situation
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20260905T100000
SUMMARY:Unrelated statistical release
END:VEVENT
END:VCALENDAR
"""
    events = parse_bls_ics(text)
    assert len(events) == 1
    assert events[0].name == "Employment Situation"
    assert events[0].impact == "HIGH"
    assert events[0].time_utc == datetime(2026, 9, 4, 12, 30, tzinfo=UTC)


def test_parse_bls_html_fallback_keeps_high_impact_release() -> None:
    text = """
    <table>
      <tr><th>Date</th><th>Time</th><th>Release</th></tr>
      <tr>
        <td>Friday, September 4, 2026</td>
        <td>08:30 AM</td>
        <td>Employment Situation for August 2026</td>
      </tr>
      <tr>
        <td>Friday, September 18, 2026</td>
        <td>10:00 AM</td>
        <td>State Employment and Unemployment (Monthly) for August 2026</td>
      </tr>
    </table>
    """
    events = parse_bls_schedule_html(text)
    assert len(events) == 1
    assert events[0].impact == "HIGH"
    assert events[0].source == "BLS"
    assert events[0].timing_quality == "OFFICIAL_HTML"
    assert events[0].time_utc == datetime(2026, 9, 4, 12, 30, tzinfo=UTC)


def test_fomc_september_2026_marker_uses_standard_2pm_et() -> None:
    event = next(item for item in fomc_events() if item.time_utc.date().isoformat() == "2026-09-16")
    assert event.time_utc == datetime(2026, 9, 16, 18, 0, tzinfo=UTC)
    assert event.timing_quality == "STANDARD_14_ET"


def test_load_mt5_context_classifies_complete_h1_h4(tmp_path) -> None:
    closes = [100.0 + index * 0.5 for index in range(100)]
    payload = {
        "symbol": "XAUUSD_o",
        "generated_at": "2026-09-03T12:00:00Z",
        "h1": {
            "highs": [value + 0.2 for value in closes],
            "lows": [value - 0.2 for value in closes],
            "closes": closes,
        },
        "h4": {
            "highs": [value + 0.4 for value in closes],
            "lows": [value - 0.4 for value in closes],
            "closes": closes,
        },
    }
    path = tmp_path / "mt5_context.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    h1, h4 = load_mt5_context(
        path,
        symbol="XAUUSD_o",
        now=datetime(2026, 9, 3, 12, 0, 30, tzinfo=UTC),
    )
    assert h1.trend == "TRENDING_UP"
    assert h4.trend == "TRENDING_UP"
    assert h1.volatility != "UNAVAILABLE"
    assert h4.volatility != "UNAVAILABLE"


def test_load_mt5_context_rejects_stale_file(tmp_path) -> None:
    closes = [100.0] * 100
    payload = {
        "symbol": "XAUUSD_o",
        "generated_at": "2026-09-03T11:00:00Z",
        "h1": {"highs": closes, "lows": closes, "closes": closes},
        "h4": {"highs": closes, "lows": closes, "closes": closes},
    }
    path = tmp_path / "mt5_context.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MarketContextError, match="stale"):
        load_mt5_context(
            path,
            symbol="XAUUSD_o",
            max_age_seconds=90,
            now=datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC),
        )
