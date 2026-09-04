from datetime import datetime, timedelta

import pytest

from meta_trader_ai.backtest import Candle
from meta_trader_ai.ea_simulator import (
    EAParameters,
    HistoricalDecision,
    simulate_ea,
)
from meta_trader_ai.ea_walk_forward_learning import candidate_grid
from meta_trader_ai.models import Action


def _candle(
    index: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
) -> Candle:
    return Candle(
        time=datetime(2026, 1, 5) + timedelta(minutes=15 * index),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        spread_points=0.0,
        real_volume=0,
    )


def _params(**changes: object) -> EAParameters:
    values = {
        "min_confidence": 75,
        "risk_percent": 0.5,
        "reward_risk_ratio": 2.0,
        "max_spread_points": 50.0,
        "max_spread_atr_ratio": 0.25,
        "max_daily_loss_percent": 1.5,
        "atr_period": 14,
        "atr_multiplier": 1.0,
        "min_stop_points": 1.0,
        "max_stop_points": 100.0,
        "swing_lookback_bars": 30,
        "swing_left_bars": 2,
        "swing_right_bars": 2,
        "structure_buffer_points": 0.0,
        "use_anti_chase": False,
        "max_extension_atr": 1.5,
        "pullback_zone_atr": 0.35,
        "pullback_max_bars": 4,
    }
    values.update(changes)
    return EAParameters(**values)


def test_two_r_win_hits_ten_dollar_goal_on_1000_balance() -> None:
    candles = [_candle(index) for index in range(30)]
    candles[25] = _candle(25, open_=100.0, high=104.5, low=99.5, close=103.0)
    decisions = [
        HistoricalDecision(
            candle_index=24,
            time=candles[24].time,
            action=Action.BUY,
            confidence=80,
            technical_score=45,
        )
    ]

    result = simulate_ea(
        candles,
        decisions,
        params=_params(),
        point_size=1.0,
        initial_balance=1000.0,
        daily_goal_usd=10.0,
    )

    assert len(result.trades) == 1
    assert result.trades[0].outcome == "TARGET"
    assert result.trades[0].pnl_r == pytest.approx(2.0)
    assert result.trades[0].pnl_usd == pytest.approx(10.0)
    assert result.metrics.end_balance == pytest.approx(1010.0)
    assert result.metrics.average_daily_pnl_usd == pytest.approx(10.0)
    assert result.metrics.daily_goal_progress_percent == pytest.approx(100.0)


def test_daily_risk_budget_blocks_fourth_full_risk_loss() -> None:
    candles = [_candle(index) for index in range(35)]
    for index in (25, 26, 27, 28):
        candles[index] = _candle(index, open_=100.0, high=100.2, low=90.0, close=100.0)
    decisions = [
        HistoricalDecision(
            candle_index=index,
            time=candles[index].time,
            action=Action.BUY,
            confidence=80,
            technical_score=45,
        )
        for index in (24, 25, 26, 27)
    ]

    result = simulate_ea(
        candles,
        decisions,
        params=_params(atr_multiplier=0.1),
        point_size=1.0,
        initial_balance=1000.0,
    )

    assert len(result.trades) == 3
    assert all(trade.pnl_r == pytest.approx(-1.0) for trade in result.trades)
    assert result.blocked["daily_risk_budget"] == 1


def test_learning_grid_never_optimizes_risk_guards() -> None:
    base = EAParameters()
    candidates = candidate_grid(base)

    assert candidates
    assert all(item.risk_percent == base.risk_percent for item in candidates)
    assert all(
        item.max_daily_loss_percent == base.max_daily_loss_percent for item in candidates
    )
    assert all(item.max_spread_points == base.max_spread_points for item in candidates)
    assert all(
        item.max_spread_atr_ratio == base.max_spread_atr_ratio for item in candidates
    )
