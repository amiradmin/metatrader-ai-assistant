from datetime import datetime, timezone

import pytest

from meta_trader_ai.models import Action, MarketSnapshot, NewsRisk, TradeHint
from meta_trader_ai.risk_controls import apply_pretrade_controls


def snapshot(
    *,
    equity: float = 1000.0,
    day_start_balance: float | None = None,
    day_realized_pnl: float | None = None,
    spread: float = 0.0001,
) -> MarketSnapshot:
    closes = [1.1000 + i * 0.001 for i in range(20)]
    return MarketSnapshot(
        symbol="EURUSD",
        timeframe="PERIOD_M15",
        generated_at=datetime.now(timezone.utc),
        bid=closes[-1],
        ask=closes[-1] + spread,
        balance=1000.0,
        equity=equity,
        day_start_balance=day_start_balance,
        day_realized_pnl=day_realized_pnl,
        closes=closes,
    )


def directional_hint(
    *,
    confidence: int = 82,
    mtf_status: str = "CONFIRM",
    tipranks_status: str = "UNAVAILABLE",
) -> TradeHint:
    return TradeHint(
        action=Action.BUY,
        symbol="EURUSD",
        confidence=confidence,
        technical_score=65,
        news_risk=NewsRisk.LOW,
        mtf_status=mtf_status,
        tipranks_status=tipranks_status,
        reasons=[],
        relevant_news=[],
        max_risk_percent=0.5,
        generated_at=datetime.now(timezone.utc),
    )


def test_daily_guard_blocks_when_next_full_risk_would_breach_limit() -> None:
    result = apply_pretrade_controls(
        snapshot(equity=989.0, day_start_balance=1000.0, day_realized_pnl=-11.0),
        directional_hint(),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.WAIT
    assert result.risk_guard_status == "DAILY_RISK_BUDGET_EXHAUSTED"
    assert result.day_drawdown_percent == pytest.approx(1.1)


def test_trade_survives_only_when_risk_mtf_and_confidence_are_valid() -> None:
    result = apply_pretrade_controls(
        snapshot(equity=995.0, day_start_balance=1000.0),
        directional_hint(confidence=82),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
        min_entry_confidence=75,
    )

    assert result.action is Action.BUY
    assert result.risk_guard_status == "OK"
    assert result.day_drawdown_percent == pytest.approx(0.5)


def test_confidence_below_strict_threshold_fails_closed() -> None:
    result = apply_pretrade_controls(
        snapshot(equity=1000.0, day_start_balance=1000.0),
        directional_hint(confidence=74),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
        min_entry_confidence=75,
    )

    assert result.action is Action.WAIT
    assert any("confidence 74 is below 75" in reason for reason in result.reasons)


def test_abnormal_spread_to_atr_hard_blocks_directional_entry() -> None:
    result = apply_pretrade_controls(
        snapshot(equity=1000.0, day_start_balance=1000.0, spread=0.0004),
        directional_hint(),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.WAIT
    assert result.spread_to_atr > 0.25
    assert any("Abnormal spread gate" in reason for reason in result.reasons)


def test_missing_day_metrics_fails_closed() -> None:
    result = apply_pretrade_controls(
        snapshot(),
        directional_hint(),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.WAIT
    assert result.risk_guard_status == "UNAVAILABLE"


@pytest.mark.parametrize("mtf_status", ["MIXED", "OPPOSE", "OBSERVE", "UNAVAILABLE"])
def test_mtf_must_fully_confirm_before_direction_survives(mtf_status: str) -> None:
    result = apply_pretrade_controls(
        snapshot(equity=1000.0, day_start_balance=1000.0),
        directional_hint(mtf_status=mtf_status),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.WAIT
    assert any("does not fully confirm" in reason for reason in result.reasons)


def test_explicit_tipranks_opposition_is_a_veto() -> None:
    result = apply_pretrade_controls(
        snapshot(equity=1000.0, day_start_balance=1000.0),
        directional_hint(tipranks_status="OPPOSE"),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.WAIT
    assert any("TipRanks" in reason and "opposes" in reason for reason in result.reasons)
