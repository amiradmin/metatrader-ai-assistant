from datetime import datetime, timedelta

import pytest

from meta_trader_ai.backtest import (
    BacktestTrade,
    Candle,
    HistoricalSignal,
    _resolve_trade,
    calculate_metrics,
    generate_signals,
    simulate_trades,
)
from meta_trader_ai.models import Action


def candle(
    minute: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    spread: float = 0.0,
) -> Candle:
    return Candle(
        time=datetime(2026, 1, 1) + timedelta(minutes=15 * minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        spread_points=spread,
        real_volume=0,
    )


def test_same_bar_stop_and_target_uses_conservative_stop_first() -> None:
    candles = [
        candle(0),
        candle(1, open_=100.0, high=105.0, low=97.0, close=102.0),
    ]
    signal = HistoricalSignal(
        candle_index=0,
        time=candles[0].time,
        action=Action.BUY,
        confidence=80,
        technical_score=45,
    )

    trade, exit_index = _resolve_trade(
        candles,
        signal,
        point_size=1.0,
        stop_loss_points=2.0,
        take_profit_points=4.0,
    )

    assert exit_index == 1
    assert trade.outcome == "STOP"
    assert trade.pnl_r == pytest.approx(-1.0)


def test_one_open_position_blocks_overlapping_signal() -> None:
    candles = [
        candle(0),
        candle(1, open_=100.0, high=101.0, low=99.0),
        candle(2, open_=100.0, high=105.0, low=99.5),
        candle(3),
    ]
    signals = [
        HistoricalSignal(0, candles[0].time, Action.BUY, 80, 40),
        HistoricalSignal(1, candles[1].time, Action.BUY, 82, 44),
    ]

    trades, candidates = simulate_trades(
        candles,
        signals,
        min_confidence=75,
        point_size=1.0,
        stop_loss_points=2.0,
        take_profit_points=4.0,
    )

    assert candidates == 2
    assert len(trades) == 1
    assert trades[0].outcome == "TARGET"
    assert trades[0].pnl_r == pytest.approx(2.0)


def test_metrics_are_reported_in_r_units() -> None:
    base = datetime(2026, 1, 1)
    trades = [
        BacktestTrade(base, base, base, Action.BUY, 80, 40, 100, 104, "TARGET", 4, 2, 1),
        BacktestTrade(base, base, base, Action.SELL, 80, -40, 100, 102, "STOP", -2, -1, 1),
        BacktestTrade(base, base, base, Action.BUY, 80, 40, 100, 104, "TARGET", 4, 2, 1),
    ]

    metrics = calculate_metrics(trades, threshold=75, candidate_signals=3)

    assert metrics.trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3 * 100)
    assert metrics.profit_factor == pytest.approx(4.0)
    assert metrics.net_r == pytest.approx(3.0)
    assert metrics.max_drawdown_r == pytest.approx(1.0)


def test_flat_market_generates_no_directional_signals() -> None:
    candles = [candle(index) for index in range(120)]

    signals = generate_signals(
        candles,
        symbol="XAUUSD_o",
        point_size=0.01,
        lookback_bars=100,
    )

    assert signals == []
