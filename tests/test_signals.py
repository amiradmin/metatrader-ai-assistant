from datetime import datetime, timezone

from meta_trader_ai.models import Action, MarketSnapshot, NewsItem, TipRanksContext
from meta_trader_ai.news import recent_news
from meta_trader_ai.signals import build_hint


def snapshot(
    closes: list[float] | None = None,
    timeframe: str = "PERIOD_M15",
) -> MarketSnapshot:
    values = closes or [1 + i / 1000 for i in range(20)]
    return MarketSnapshot(
        symbol="EURUSD",
        timeframe=timeframe,
        generated_at=datetime.now(timezone.utc),
        bid=values[-1],
        ask=values[-1] + 0.0001,
        balance=1000,
        equity=1000,
        closes=values,
    )


def tipranks_context(bullish: bool) -> TipRanksContext:
    return TipRanksContext(
        symbol="EURUSD",
        price=1.20 if bullish else 0.90,
        change_percentage=0.50 if bullish else -0.50,
        price_avg_50=1.10 if bullish else 1.00,
        price_avg_200=1.00 if bullish else 1.10,
        updated_at=datetime.now(timezone.utc),
    )


def test_high_impact_news_forces_wait() -> None:
    news = [
        NewsItem(
            source="Federal Reserve",
            title="FOMC interest rate decision",
            url="https://example.test",
            currencies={"USD"},
            impact_score=80,
        )
    ]
    hint = build_hint(snapshot(), news, 0.5)
    assert hint.action is Action.WAIT
    assert hint.confidence == 85


def test_stale_news_is_excluded() -> None:
    now = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
    old_item = NewsItem(
        source="Federal Reserve",
        title="FOMC interest rate decision",
        url="https://example.test/old",
        published_at=datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc),
        currencies={"USD"},
        impact_score=80,
    )
    assert recent_news([old_item], lookback_hours=24, now=now) == []


def test_m15_confidence_is_dynamic_and_can_clear_threshold() -> None:
    strong_uptrend = [1 + i / 1000 for i in range(20)]
    hint = build_hint(snapshot(strong_uptrend), [], 0.5)
    assert hint.technical_score >= 30
    assert hint.confidence >= 70
    assert hint.action is Action.BUY


def test_flat_m15_market_waits_with_lower_confidence() -> None:
    flat = [1.1000] * 20
    hint = build_hint(snapshot(flat), [], 0.5)
    assert hint.technical_score == 0
    assert hint.confidence < 70
    assert hint.action is Action.WAIT


def test_non_m15_timeframe_is_read_only_wait() -> None:
    hint = build_hint(snapshot(timeframe="PERIOD_H1"), [], 0.5)
    assert hint.action is Action.WAIT
    assert hint.confidence == 55
    assert any("M15-first" in reason for reason in hint.reasons)


def test_tipranks_can_confirm_but_not_create_m15_direction() -> None:
    strong_uptrend = [1 + i / 1000 for i in range(20)]
    base = build_hint(snapshot(strong_uptrend), [], 0.5)
    confirmed = build_hint(
        snapshot(strong_uptrend),
        [],
        0.5,
        tipranks_context=tipranks_context(bullish=True),
    )
    opposed = build_hint(
        snapshot(strong_uptrend),
        [],
        0.5,
        tipranks_context=tipranks_context(bullish=False),
    )

    assert confirmed.action is Action.BUY
    assert confirmed.confidence > base.confidence
    assert opposed.confidence < base.confidence

    flat = [1.1000] * 20
    neutral_hint = build_hint(
        snapshot(flat),
        [],
        0.5,
        tipranks_context=tipranks_context(bullish=True),
    )
    assert neutral_hint.action is Action.WAIT
