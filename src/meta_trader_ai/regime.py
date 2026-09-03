"""Market-regime diagnostics used by historical backtests only for now.

The classifier is deliberately causal: it uses candles available up to the
signal candle and never reads future bars.  Live trading code does not import
this module yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean
from typing import Protocol, Sequence


class OhlcCandle(Protocol):
    """Minimal candle shape required by the regime classifier."""

    high: float
    low: float
    close: float


class TrendRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"


class VolatilityRegime(StrEnum):
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    NORMAL_VOLATILITY = "NORMAL_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"


@dataclass(frozen=True, slots=True)
class RegimeState:
    """Causal trend + volatility state for one completed candle."""

    trend: TrendRegime
    volatility: VolatilityRegime
    efficiency_ratio: float
    net_move_atr: float
    atr: float
    volatility_ratio: float


def _true_ranges(candles: Sequence[OhlcCandle]) -> list[float]:
    result: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        result.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return result


def classify_regime(
    candles: Sequence[OhlcCandle],
    *,
    trend_bars: int = 20,
    atr_bars: int = 14,
    volatility_baseline_bars: int = 50,
    efficiency_min: float = 0.28,
    trend_move_atr_min: float = 1.25,
    high_volatility_ratio: float = 1.35,
    low_volatility_ratio: float = 0.75,
) -> RegimeState:
    """Classify a completed historical window without look-ahead.

    Trend uses Kaufman-style efficiency plus net displacement measured in ATR.
    Volatility compares recent ATR with the mean true range of the preceding
    baseline window.  The thresholds are intentionally fixed heuristics for the
    first diagnostic pass; they are not yet production parameters.
    """
    minimum = max(
        trend_bars + 1,
        atr_bars + volatility_baseline_bars + 1,
    )
    if len(candles) < minimum:
        raise ValueError(f"At least {minimum} candles are required")
    if trend_bars < 2 or atr_bars < 2 or volatility_baseline_bars < 5:
        raise ValueError("Regime lookback parameters are too small")
    if not 0.0 <= efficiency_min <= 1.0:
        raise ValueError("efficiency_min must be between 0 and 1")
    if trend_move_atr_min <= 0:
        raise ValueError("trend_move_atr_min must be positive")
    if not 0 < low_volatility_ratio < high_volatility_ratio:
        raise ValueError("Volatility ratio thresholds are invalid")

    true_ranges = _true_ranges(candles)
    recent_atr = fmean(true_ranges[-atr_bars:])
    recent_atr = max(recent_atr, 1e-12)

    baseline_end = len(true_ranges) - atr_bars
    baseline_start = baseline_end - volatility_baseline_bars
    baseline_tr = fmean(true_ranges[baseline_start:baseline_end])
    baseline_tr = max(baseline_tr, 1e-12)
    volatility_ratio = recent_atr / baseline_tr

    trend_closes = [candle.close for candle in candles[-(trend_bars + 1) :]]
    net_move = trend_closes[-1] - trend_closes[0]
    travelled = sum(
        abs(current - previous)
        for previous, current in zip(trend_closes, trend_closes[1:])
    )
    efficiency_ratio = abs(net_move) / travelled if travelled > 1e-12 else 0.0
    net_move_atr = net_move / recent_atr

    trending = (
        efficiency_ratio >= efficiency_min
        and abs(net_move_atr) >= trend_move_atr_min
    )
    if trending and net_move > 0:
        trend = TrendRegime.TRENDING_UP
    elif trending and net_move < 0:
        trend = TrendRegime.TRENDING_DOWN
    else:
        trend = TrendRegime.RANGING

    if volatility_ratio >= high_volatility_ratio:
        volatility = VolatilityRegime.HIGH_VOLATILITY
    elif volatility_ratio <= low_volatility_ratio:
        volatility = VolatilityRegime.LOW_VOLATILITY
    else:
        volatility = VolatilityRegime.NORMAL_VOLATILITY

    return RegimeState(
        trend=trend,
        volatility=volatility,
        efficiency_ratio=efficiency_ratio,
        net_move_atr=net_move_atr,
        atr=recent_atr,
        volatility_ratio=volatility_ratio,
    )
