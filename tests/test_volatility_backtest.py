from dataclasses import dataclass
from datetime import datetime

import pytest

from meta_trader_ai.backtest import HistoricalSignal
from meta_trader_ai.models import Action
from meta_trader_ai.regime import RegimeState, TrendRegime, VolatilityRegime
from meta_trader_ai.regime_backtest import RegimeSignal
from meta_trader_ai.volatility_backtest import filter_labelled_signals


def _item(
    action: Action,
    trend: TrendRegime,
    volatility: VolatilityRegime,
) -> RegimeSignal:
    signal = HistoricalSignal(
        candle_index=100,
        time=datetime(2026, 9, 3, 10, 0),
        action=action,
        confidence=75,
        technical_score=40 if action is Action.BUY else -40,
    )
    regime = RegimeState(
        trend=trend,
        volatility=volatility,
        efficiency_ratio=0.8,
        net_move_atr=2.0 if trend is TrendRegime.TRENDING_UP else -2.0,
        atr=3.0,
        volatility_ratio=1.0,
    )
    return RegimeSignal(signal=signal, regime=regime)


def test_no_high_vol_removes_only_high_volatility() -> None:
    low = _item(Action.BUY, TrendRegime.TRENDING_UP, VolatilityRegime.LOW_VOLATILITY)
    high = _item(Action.SELL, TrendRegime.TRENDING_DOWN, VolatilityRegime.HIGH_VOLATILITY)
    result = filter_labelled_signals([low, high], "NO_HIGH_VOL")
    assert result == [low.signal]


def test_low_vol_only_keeps_low_volatility() -> None:
    low = _item(Action.BUY, TrendRegime.TRENDING_UP, VolatilityRegime.LOW_VOLATILITY)
    normal = _item(Action.BUY, TrendRegime.TRENDING_UP, VolatilityRegime.NORMAL_VOLATILITY)
    result = filter_labelled_signals([low, normal], "LOW_VOL_ONLY")
    assert result == [low.signal]


def test_trend_and_low_requires_both_conditions() -> None:
    allowed = _item(Action.BUY, TrendRegime.TRENDING_UP, VolatilityRegime.LOW_VOLATILITY)
    wrong_trend = _item(Action.BUY, TrendRegime.TRENDING_DOWN, VolatilityRegime.LOW_VOLATILITY)
    high = _item(Action.BUY, TrendRegime.TRENDING_UP, VolatilityRegime.HIGH_VOLATILITY)
    result = filter_labelled_signals([allowed, wrong_trend, high], "TREND_AND_LOW")
    assert result == [allowed.signal]


def test_unknown_filter_mode_fails_fast() -> None:
    with pytest.raises(ValueError, match="Unknown filter mode"):
        filter_labelled_signals([], "NOT_A_MODE")
