"""Tests for the independent FAST_SCALP_M1 signal engine."""

from datetime import datetime, timezone

from meta_trader_ai.fast_scalp import FastScalpSnapshot, build_fast_scalp_hint
from meta_trader_ai.models import Action, NewsCoverage, NewsRisk


def _bullish_snapshot(*, positions_total: int = 0) -> FastScalpSnapshot:
    closes = [4000.0 + index * 0.12 for index in range(40)]
    opens = [value - 0.05 for value in closes]
    highs = [value + 0.10 for value in closes]
    lows = [value - 0.15 for value in closes]
    volumes = [100] * 39 + [160]
    m5_closes = [3990.0 + index * 0.20 for index in range(30)]

    return FastScalpSnapshot(
        symbol="XAUUSD_o",
        timeframe="PERIOD_M1",
        generated_at=datetime.now(timezone.utc),
        bid=closes[-1],
        ask=closes[-1] + 0.01,
        balance=1_000.0,
        equity=1_000.0,
        positions_total=positions_total,
        day_start_balance=1_000.0,
        day_realized_pnl=0.0,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        tick_volumes=volumes,
        m5_closes=m5_closes,
    )


def test_strong_bullish_setup_can_produce_buy() -> None:
    hint = build_fast_scalp_hint(
        _bullish_snapshot(),
        [],
        news_coverage=NewsCoverage.COMPLETE,
    )

    assert hint.profile == "FAST_SCALP_M1"
    assert hint.action is Action.BUY
    assert hint.confidence >= 72
    assert hint.trend_m5 == "BULLISH"
    assert hint.risk_guard_status == "OK"
    assert hint.max_open_positions == 2
    assert hint.max_risk_percent == 0.25


def test_two_open_positions_hard_block_new_entry() -> None:
    hint = build_fast_scalp_hint(
        _bullish_snapshot(positions_total=2),
        [],
        max_open_positions=2,
        news_coverage=NewsCoverage.COMPLETE,
    )

    assert hint.action is Action.WAIT
    assert hint.risk_guard_status == "POSITION_LIMIT"
    assert hint.positions_total == 2
    assert any("Open-position cap reached" in reason for reason in hint.reasons)


def test_m5_opposition_blocks_directional_m1_setup() -> None:
    snapshot = _bullish_snapshot().model_copy(
        update={"m5_closes": [4010.0 - index * 0.20 for index in range(30)]}
    )

    hint = build_fast_scalp_hint(
        snapshot,
        [],
        news_coverage=NewsCoverage.COMPLETE,
    )

    assert hint.action is Action.WAIT
    assert hint.trend_m5 == "BEARISH"
    assert hint.risk_guard_status == "M5_OPPOSE"


def test_unavailable_news_degrades_confidence_without_high_news_hard_stop() -> None:
    snapshot = _bullish_snapshot()
    complete = build_fast_scalp_hint(
        snapshot,
        [],
        news_coverage=NewsCoverage.COMPLETE,
    )
    unavailable = build_fast_scalp_hint(
        snapshot,
        [],
        news_coverage=NewsCoverage.UNAVAILABLE,
        failed_news_sources=4,
    )

    assert unavailable.news_risk is NewsRisk.UNKNOWN
    assert unavailable.failed_news_sources == 4
    assert unavailable.confidence < complete.confidence
    assert unavailable.risk_guard_status != "HIGH_IMPACT_NEWS"
    assert any("News coverage is unavailable" in reason for reason in unavailable.reasons)


def test_wide_spread_is_hard_blocked() -> None:
    base = _bullish_snapshot()
    snapshot = base.model_copy(update={"ask": base.bid + 0.10})

    hint = build_fast_scalp_hint(
        snapshot,
        [],
        max_spread_atr_ratio=0.18,
        news_coverage=NewsCoverage.COMPLETE,
    )

    assert hint.action is Action.WAIT
    assert hint.risk_guard_status == "SPREAD_TOO_WIDE"
