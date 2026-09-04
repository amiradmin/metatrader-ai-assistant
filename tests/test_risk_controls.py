from datetime import datetime, timezone

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


def directional_hint() -> TradeHint:
    return TradeHint(
        action=Action.BUY,
        symbol="EURUSD",
        confidence=82,
        technical_score=65,
        news_risk=NewsRisk.LOW,
        reasons=[],
        relevant_news=[],
        max_risk_percent=0.5,
        generated_at=datetime.now(timezone.utc),
    )


def test_daily_guard_blocks_when_next_full_risk_would_breach_limit() -> None:
    result = apply_pretrade_controls(
        snapshot(
            equity=989.0,
            day_start_balance=1000.0,
            day_realized_pnl=-11.0,
        ),
        directional_hint(),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.WAIT
    assert result.risk_guard_status == "DAILY_RISK_BUDGET_EXHAUSTED"
    assert result.day_drawdown_percent == 1.1
    assert any("risk budget exhausted" in reason.lower() for reason in result.reasons)


def test_daily_guard_allows_trade_when_loss_budget_remains() -> None:
    result = apply_pretrade_controls(
        snapshot(equity=995.0, day_start_balance=1000.0),
        directional_hint(),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.BUY
    assert result.risk_guard_status == "OK"
    assert result.day_drawdown_percent == 0.5


def test_abnormal_spread_to_atr_hard_blocks_directional_entry() -> None:
    result = apply_pretrade_controls(
        snapshot(
            equity=1000.0,
            day_start_balance=1000.0,
            spread=0.0004,
        ),
        directional_hint(),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.WAIT
    assert result.spread_to_atr > 0.25
    assert any("Abnormal spread gate" in reason for reason in result.reasons)


def test_missing_day_metrics_degrades_guard_without_blocking_trade() -> None:
    result = apply_pretrade_controls(
        snapshot(),
        directional_hint(),
        max_daily_loss_percent=1.5,
        max_spread_atr_ratio=0.25,
    )

    assert result.action is Action.BUY
    assert result.risk_guard_status == "UNAVAILABLE"
