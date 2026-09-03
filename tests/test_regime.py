from dataclasses import dataclass

from meta_trader_ai.regime import (
    TrendRegime,
    VolatilityRegime,
    classify_regime,
)


@dataclass(frozen=True)
class TestCandle:
    high: float
    low: float
    close: float


def _trend_candles(direction: float, count: int = 100) -> list[TestCandle]:
    candles: list[TestCandle] = []
    close = 100.0
    for _ in range(count):
        close += direction
        candles.append(TestCandle(high=close + 0.5, low=close - 0.5, close=close))
    return candles


def test_classifier_detects_clean_uptrend() -> None:
    state = classify_regime(_trend_candles(1.0))
    assert state.trend is TrendRegime.TRENDING_UP
    assert state.volatility is VolatilityRegime.NORMAL_VOLATILITY
    assert state.efficiency_ratio > 0.9
    assert state.net_move_atr > 1.25


def test_classifier_detects_clean_downtrend() -> None:
    state = classify_regime(_trend_candles(-1.0))
    assert state.trend is TrendRegime.TRENDING_DOWN
    assert state.volatility is VolatilityRegime.NORMAL_VOLATILITY
    assert state.efficiency_ratio > 0.9
    assert state.net_move_atr < -1.25


def test_classifier_detects_choppy_range() -> None:
    candles: list[TestCandle] = []
    for index in range(100):
        close = 100.0 + (1.0 if index % 2 else -1.0)
        candles.append(TestCandle(high=close + 0.5, low=close - 0.5, close=close))

    state = classify_regime(candles)
    assert state.trend is TrendRegime.RANGING
    assert state.efficiency_ratio < 0.1


def test_classifier_detects_recent_high_volatility() -> None:
    candles: list[TestCandle] = []
    close = 100.0
    for index in range(100):
        close += 0.1
        width = 0.5 if index < 86 else 2.5
        candles.append(TestCandle(high=close + width, low=close - width, close=close))

    state = classify_regime(candles)
    assert state.volatility is VolatilityRegime.HIGH_VOLATILITY
    assert state.volatility_ratio >= 1.35
