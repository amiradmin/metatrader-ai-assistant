"""Course-informed market-cycle gates for the M15 decision engine.

This module translates a small set of rules from the user's trading course into
causal, testable features.  It is deliberately conservative: it may preserve or
block an existing BUY/SELL hint, but it never manufactures a new direction.

Source-derived ideas:
- decision priority is trend -> level -> slope/power;
- avoid trading against a strong trend or spike;
- in a range, prefer BUY near the lower edge and SELL near the upper edge;
- avoid the middle of a range;
- a range breakout is treated as valid only when all three course conditions
  are present: a large breakout candle, >50% penetration beyond the boundary,
  and a break beyond the wick-defined range extreme.

Engineering proxies (not verbatim course rules): numerical regime thresholds,
20% range-edge bands, touch tolerance, and the causal lookback windows.  These
must be validated by backtest/walk-forward before production weighting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean

from meta_trader_ai.models import Action, MarketSnapshot, TradeHint


class CourseRegime(StrEnum):
    STRONG_UPTREND = "STRONG_UPTREND"
    WEAK_UPTREND = "WEAK_UPTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    WEAK_DOWNTREND = "WEAK_DOWNTREND"
    RANGE = "RANGE"
    SPIKE_UP = "SPIKE_UP"
    SPIKE_DOWN = "SPIKE_DOWN"
    UNKNOWN = "UNKNOWN"


class RangeZone(StrEnum):
    LOWER_EDGE = "LOWER_EDGE"
    MIDDLE = "MIDDLE"
    UPPER_EDGE = "UPPER_EDGE"
    OUTSIDE = "OUTSIDE"
    UNAVAILABLE = "UNAVAILABLE"


class BreakoutStatus(StrEnum):
    NONE = "NONE"
    VALID_UP = "VALID_UP"
    VALID_DOWN = "VALID_DOWN"
    FAKE_UP = "FAKE_UP"
    FAKE_DOWN = "FAKE_DOWN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CourseDecisionConfig:
    """Experimental causal mapping from course concepts to numeric features."""

    range_lookback: int = 24
    trend_lookback: int = 20
    range_edge_fraction: float = 0.20
    touch_tolerance_atr: float = 0.20
    breakout_step_bars: int = 5
    breakout_body_multiple: float = 1.50
    weak_efficiency_min: float = 0.25
    strong_efficiency_min: float = 0.45
    weak_move_atr_min: float = 1.00
    strong_move_atr_min: float = 2.00
    spike_body_atr_min: float = 1.80


@dataclass(frozen=True, slots=True)
class CourseDecisionState:
    regime: CourseRegime
    range_zone: RangeZone
    breakout_status: BreakoutStatus
    lower_touch_count: int
    upper_touch_count: int
    atr: float
    efficiency_ratio: float
    net_move_atr: float
    range_low: float | None
    range_high: float | None


def _has_ohlc(snapshot: MarketSnapshot) -> bool:
    size = len(snapshot.closes)
    return (
        size >= 20
        and len(snapshot.opens) == size
        and len(snapshot.highs) == size
        and len(snapshot.lows) == size
    )


def _true_ranges(snapshot: MarketSnapshot) -> list[float]:
    result: list[float] = []
    for index in range(1, len(snapshot.closes)):
        high = snapshot.highs[index]
        low = snapshot.lows[index]
        previous_close = snapshot.closes[index - 1]
        result.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    return result


def _atr(snapshot: MarketSnapshot, period: int = 14) -> float:
    ranges = _true_ranges(snapshot)
    sample = ranges[-period:]
    value = fmean(sample) if sample else 0.0
    return max(value, 1e-12)


def _trend_metrics(
    snapshot: MarketSnapshot,
    *,
    lookback: int,
    atr: float,
) -> tuple[float, float]:
    closes = snapshot.closes[-(lookback + 1) :]
    if len(closes) < 3:
        return 0.0, 0.0
    net_move = closes[-1] - closes[0]
    travelled = sum(
        abs(current - previous)
        for previous, current in zip(closes, closes[1:])
    )
    efficiency = abs(net_move) / travelled if travelled > 1e-12 else 0.0
    return efficiency, net_move / atr


def _classify_regime(
    snapshot: MarketSnapshot,
    config: CourseDecisionConfig,
    *,
    atr: float,
) -> tuple[CourseRegime, float, float]:
    efficiency, net_move_atr = _trend_metrics(
        snapshot,
        lookback=config.trend_lookback,
        atr=atr,
    )

    latest_body = snapshot.closes[-1] - snapshot.opens[-1]
    latest_body_atr = abs(latest_body) / atr
    if latest_body_atr >= config.spike_body_atr_min:
        if latest_body > 0:
            return CourseRegime.SPIKE_UP, efficiency, net_move_atr
        if latest_body < 0:
            return CourseRegime.SPIKE_DOWN, efficiency, net_move_atr

    strong = (
        efficiency >= config.strong_efficiency_min
        and abs(net_move_atr) >= config.strong_move_atr_min
    )
    weak = (
        efficiency >= config.weak_efficiency_min
        and abs(net_move_atr) >= config.weak_move_atr_min
    )
    if strong and net_move_atr > 0:
        regime = CourseRegime.STRONG_UPTREND
    elif strong and net_move_atr < 0:
        regime = CourseRegime.STRONG_DOWNTREND
    elif weak and net_move_atr > 0:
        regime = CourseRegime.WEAK_UPTREND
    elif weak and net_move_atr < 0:
        regime = CourseRegime.WEAK_DOWNTREND
    else:
        regime = CourseRegime.RANGE
    return regime, efficiency, net_move_atr


def _range_context(
    snapshot: MarketSnapshot,
    config: CourseDecisionConfig,
    *,
    atr: float,
) -> tuple[RangeZone, BreakoutStatus, int, int, float | None, float | None]:
    lookback = max(8, config.range_lookback)
    if len(snapshot.closes) < lookback + 1:
        return RangeZone.UNAVAILABLE, BreakoutStatus.UNAVAILABLE, 0, 0, None, None

    prior_start = len(snapshot.closes) - lookback - 1
    prior_end = len(snapshot.closes) - 1
    prior_highs = snapshot.highs[prior_start:prior_end]
    prior_lows = snapshot.lows[prior_start:prior_end]
    prior_opens = snapshot.opens[prior_start:prior_end]
    prior_closes = snapshot.closes[prior_start:prior_end]
    if not prior_highs or not prior_lows:
        return RangeZone.UNAVAILABLE, BreakoutStatus.UNAVAILABLE, 0, 0, None, None

    range_high = max(prior_highs)
    range_low = min(prior_lows)
    width = range_high - range_low
    if width <= 1e-12:
        return RangeZone.UNAVAILABLE, BreakoutStatus.UNAVAILABLE, 0, 0, range_low, range_high

    close = snapshot.closes[-1]
    if close < range_low or close > range_high:
        zone = RangeZone.OUTSIDE
    else:
        position = (close - range_low) / width
        edge = min(0.45, max(0.05, config.range_edge_fraction))
        if position <= edge:
            zone = RangeZone.LOWER_EDGE
        elif position >= 1.0 - edge:
            zone = RangeZone.UPPER_EDGE
        else:
            zone = RangeZone.MIDDLE

    tolerance = max(atr * config.touch_tolerance_atr, width * 0.01)
    lower_touches = sum(abs(low - range_low) <= tolerance for low in prior_lows)
    upper_touches = sum(abs(high - range_high) <= tolerance for high in prior_highs)

    latest_open = snapshot.opens[-1]
    latest_high = snapshot.highs[-1]
    latest_low = snapshot.lows[-1]
    latest_close = snapshot.closes[-1]
    latest_body = abs(latest_close - latest_open)
    latest_range = max(latest_high - latest_low, 1e-12)

    step_bars = max(2, config.breakout_step_bars)
    body_sample = [
        abs(close_value - open_value)
        for open_value, close_value in zip(prior_opens[-step_bars:], prior_closes[-step_bars:])
    ]
    step_body = max(fmean(body_sample) if body_sample else atr, 1e-12)
    large_body = latest_body >= config.breakout_body_multiple * step_body

    up_candidate = latest_high > range_high
    down_candidate = latest_low < range_low

    if up_candidate:
        beyond_fraction = max(0.0, latest_high - max(latest_low, range_high)) / latest_range
        penetration = beyond_fraction > 0.50
        wick_extreme_broken = latest_high > range_high
        closes_outside = latest_close > range_high
        if large_body and penetration and wick_extreme_broken and closes_outside:
            breakout = BreakoutStatus.VALID_UP
        else:
            breakout = BreakoutStatus.FAKE_UP
    elif down_candidate:
        beyond_fraction = max(0.0, min(latest_high, range_low) - latest_low) / latest_range
        penetration = beyond_fraction > 0.50
        wick_extreme_broken = latest_low < range_low
        closes_outside = latest_close < range_low
        if large_body and penetration and wick_extreme_broken and closes_outside:
            breakout = BreakoutStatus.VALID_DOWN
        else:
            breakout = BreakoutStatus.FAKE_DOWN
    else:
        breakout = BreakoutStatus.NONE

    return zone, breakout, lower_touches, upper_touches, range_low, range_high


def inspect_course_decision_tree(
    snapshot: MarketSnapshot,
    config: CourseDecisionConfig | None = None,
) -> CourseDecisionState:
    """Return causal course-inspired features from completed M15 candles."""
    config = config or CourseDecisionConfig()
    if not _has_ohlc(snapshot):
        return CourseDecisionState(
            regime=CourseRegime.UNKNOWN,
            range_zone=RangeZone.UNAVAILABLE,
            breakout_status=BreakoutStatus.UNAVAILABLE,
            lower_touch_count=0,
            upper_touch_count=0,
            atr=0.0,
            efficiency_ratio=0.0,
            net_move_atr=0.0,
            range_low=None,
            range_high=None,
        )

    atr = _atr(snapshot)
    regime, efficiency, net_move_atr = _classify_regime(snapshot, config, atr=atr)
    zone, breakout, lower_touches, upper_touches, range_low, range_high = _range_context(
        snapshot,
        config,
        atr=atr,
    )
    return CourseDecisionState(
        regime=regime,
        range_zone=zone,
        breakout_status=breakout,
        lower_touch_count=lower_touches,
        upper_touch_count=upper_touches,
        atr=atr,
        efficiency_ratio=efficiency,
        net_move_atr=net_move_atr,
        range_low=range_low,
        range_high=range_high,
    )


def _block(hint: TradeHint, reason: str) -> None:
    if hint.action in {Action.BUY, Action.SELL}:
        hint.action = Action.WAIT
        hint.course_tree_status = "BLOCK"
        hint.reasons.append(reason)


def apply_course_decision_tree(
    snapshot: MarketSnapshot,
    hint: TradeHint,
    config: CourseDecisionConfig | None = None,
) -> TradeHint:
    """Apply course-informed gates without ever creating a new direction."""
    state = inspect_course_decision_tree(snapshot, config)
    hint.course_regime = state.regime.value
    hint.course_range_zone = state.range_zone.value
    hint.course_breakout_status = state.breakout_status.value
    hint.course_lower_touch_count = state.lower_touch_count
    hint.course_upper_touch_count = state.upper_touch_count
    hint.course_tree_status = "OBSERVE" if hint.action is Action.WAIT else "CONFIRM"
    hint.reasons.append(
        "Course tree: "
        f"regime={state.regime.value}, zone={state.range_zone.value}, "
        f"breakout={state.breakout_status.value}, "
        f"touches(L/U)={state.lower_touch_count}/{state.upper_touch_count}, "
        f"efficiency={state.efficiency_ratio:.2f}, move={state.net_move_atr:.2f} ATR."
    )

    action = hint.action
    if action not in {Action.BUY, Action.SELL}:
        return hint

    if state.regime in {CourseRegime.UNKNOWN}:
        _block(hint, "Course tree blocked direction because OHLC context is unavailable.")
        return hint

    if state.regime in {CourseRegime.STRONG_UPTREND, CourseRegime.SPIKE_UP} and action is Action.SELL:
        _block(hint, f"Course trend priority blocked SELL against {state.regime.value}.")
        return hint
    if state.regime in {CourseRegime.STRONG_DOWNTREND, CourseRegime.SPIKE_DOWN} and action is Action.BUY:
        _block(hint, f"Course trend priority blocked BUY against {state.regime.value}.")
        return hint

    if state.regime is not CourseRegime.RANGE:
        return hint

    if state.breakout_status is BreakoutStatus.VALID_UP:
        if action is Action.SELL:
            _block(hint, "Validated upside range breakout blocked SELL.")
        return hint
    if state.breakout_status is BreakoutStatus.VALID_DOWN:
        if action is Action.BUY:
            _block(hint, "Validated downside range breakout blocked BUY.")
        return hint
    if state.breakout_status is BreakoutStatus.FAKE_UP and action is Action.BUY:
        _block(hint, "Fake upside breakout blocked BUY until price re-establishes range context.")
        return hint
    if state.breakout_status is BreakoutStatus.FAKE_DOWN and action is Action.SELL:
        _block(hint, "Fake downside breakout blocked SELL until price re-establishes range context.")
        return hint

    if state.range_zone is RangeZone.MIDDLE:
        _block(hint, "Course range rule blocked entry in the middle of the range.")
    elif action is Action.BUY and state.range_zone is not RangeZone.LOWER_EDGE:
        _block(hint, "Course range rule allows BUY only near the lower range edge.")
    elif action is Action.SELL and state.range_zone is not RangeZone.UPPER_EDGE:
        _block(hint, "Course range rule allows SELL only near the upper range edge.")
    return hint
