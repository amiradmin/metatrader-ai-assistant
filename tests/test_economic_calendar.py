from datetime import UTC, datetime, timedelta

from meta_trader_ai.economic_calendar import (
    CalendarEvent,
    calendar_news_items,
    parse_calendar_payload,
)


def test_parse_forex_factory_payload() -> None:
    events = parse_calendar_payload(
        [
            {
                "title": "CPI m/m",
                "country": "USD",
                "date": "2026-09-03T08:30:00-04:00",
                "impact": "High",
                "forecast": "0.2%",
                "previous": "0.1%",
            }
        ]
    )

    assert len(events) == 1
    assert events[0].currency == "USD"
    assert events[0].impact == "HIGH"
    assert events[0].scheduled_at.tzinfo is not None


def test_high_impact_usd_blocks_xauusd_inside_window() -> None:
    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    event = CalendarEvent(
        title="Non-Farm Employment Change",
        currency="USD",
        scheduled_at=now + timedelta(minutes=20),
        impact="HIGH",
    )

    items = calendar_news_items("XAUUSD_o", [event], now=now)

    assert len(items) == 1
    assert items[0].impact_score == 100
    assert items[0].currencies == {"USD"}


def test_high_impact_event_outside_window_does_not_block() -> None:
    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    event = CalendarEvent(
        title="CPI m/m",
        currency="USD",
        scheduled_at=now + timedelta(minutes=45),
        impact="HIGH",
    )

    assert calendar_news_items("XAUUSD_o", [event], now=now) == []


def test_medium_event_becomes_medium_news_risk_input() -> None:
    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    event = CalendarEvent(
        title="Unemployment Claims",
        currency="USD",
        scheduled_at=now + timedelta(minutes=10),
        impact="MEDIUM",
    )

    items = calendar_news_items("EURUSD", [event], now=now)

    assert len(items) == 1
    assert items[0].impact_score == 25


def test_unrelated_currency_is_ignored() -> None:
    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    event = CalendarEvent(
        title="Cash Rate",
        currency="AUD",
        scheduled_at=now + timedelta(minutes=5),
        impact="HIGH",
    )

    assert calendar_news_items("XAUUSD_o", [event], now=now) == []
