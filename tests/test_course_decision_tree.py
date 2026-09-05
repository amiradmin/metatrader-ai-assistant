from datetime import UTC, datetime

from meta_trader_ai.course_decision_tree import (
    BreakoutStatus,
    CourseRegime,
    RangeZone,
    apply_course_decision_tree,
    inspect_course_decision_tree,
)
from meta_trader_ai.models import Action, MarketSnapshot, NewsRisk, TradeHint


def _snapshot(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="XAUUSD_o",
        timeframe="PERIOD_M15",
        generated_at=datetime.now(UTC),
        bid=closes[-1],
        ask=closes[-1] + 0.2,
        balance=1000.0,
        equity=1000.0,
        day_start_balance=1000.0,
        day_realized_pnl=0.0,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
    )


def _hint(action: Action) -> TradeHint:
    return TradeHint(
        action=action,
        symbol="XAUUSD_o",
        confidence=80,
        technical_score=60 if action is Action.BUY else -60,
        news_risk=NewsRisk.LOW,
        risk_guard_status="OK",
        mtf_status="CONFIRM",
        reasons=[],
        relevant_news=[],
        max_risk_percent=0.5,
        generated_at=datetime.now(UTC),
    )


def _ranging_snapshot(
    *,
    last_open: float = 100.0,
    last_high: float = 100.2,
    last_low: float = 99.8,
    last_close: float = 100.0,
) -> MarketSnapshot:
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for index in range(30):
        close = 99.8 if index % 2 == 0 else 100.2
        open_value = 100.0 if index % 2 == 0 else 100.05
        opens.append(open_value)
        highs.append(101.0)
        lows.append(99.0)
        closes.append(close)
    opens.append(last_open)
    highs.append(last_high)
    lows.append(last_low)
    closes.append(last_close)
    return _snapshot(opens, highs, lows, closes)


def test_middle_of_range_blocks_directional_entry() -> None:
    snapshot = _ranging_snapshot()
    state = inspect_course_decision_tree(snapshot)
    assert state.regime is CourseRegime.RANGE
    assert state.range_zone is RangeZone.MIDDLE

    hint = apply_course_decision_tree(snapshot, _hint(Action.BUY))
    assert hint.action is Action.WAIT
    assert hint.course_tree_status == "BLOCK"
    assert any("middle of the range" in reason for reason in hint.reasons)


def test_strong_uptrend_blocks_sell() -> None:
    closes = [100.0 + index * 0.5 for index in range(32)]
    opens = [close - 0.2 for close in closes]
    highs = [close + 0.1 for close in closes]
    lows = [open_value - 0.1 for open_value in opens]
    snapshot = _snapshot(opens, highs, lows, closes)

    state = inspect_course_decision_tree(snapshot)
    assert state.regime is CourseRegime.STRONG_UPTREND

    hint = apply_course_decision_tree(snapshot, _hint(Action.SELL))
    assert hint.action is Action.WAIT
    assert any("blocked SELL" in reason for reason in hint.reasons)


def test_three_condition_breakout_is_valid_up() -> None:
    snapshot = _ranging_snapshot(
        last_open=100.5,
        last_high=103.0,
        last_low=100.4,
        last_close=102.8,
    )
    state = inspect_course_decision_tree(snapshot)
    assert state.breakout_status is BreakoutStatus.VALID_UP
    assert state.range_zone is RangeZone.OUTSIDE


def test_small_wick_break_is_fake_up() -> None:
    snapshot = _ranging_snapshot(
        last_open=100.70,
        last_high=101.25,
        last_low=100.50,
        last_close=100.80,
    )
    state = inspect_course_decision_tree(snapshot)
    assert state.breakout_status is BreakoutStatus.FAKE_UP


def test_course_tree_never_manufactures_direction_from_wait() -> None:
    snapshot = _ranging_snapshot(last_close=99.1, last_low=99.0, last_high=99.4)
    hint = apply_course_decision_tree(snapshot, _hint(Action.WAIT))
    assert hint.action is Action.WAIT
    assert hint.course_tree_status == "OBSERVE"
