"""Historical simulator for the unified MetaTraderAI EA.

The simulator replays real MT5-exported M15 OHLC/spread data causally. It uses
exactly the current Python signal engine for directional decisions and mirrors
the unified EA's execution guards closely: confidence threshold, recorded
spread, spread/ATR guard, daily-loss budget, anti-chase/pullback timing,
ATR+confirmed-swing stops, fixed reward/risk target and one open position.

Historical news, TipRanks and exact tick-by-tick intrabar paths are not
reconstructed. Same-bar stop+target touches are therefore resolved STOP-first
(conservative). Money P/L assumes continuous risk sizing at the configured
risk percent; broker lot-step/minimum constraints are not reconstructed.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from meta_trader_ai.backtest import Candle, load_candles
from meta_trader_ai.models import Action, MarketSnapshot
from meta_trader_ai.signals import build_hint


@dataclass(frozen=True, slots=True)
class HistoricalDecision:
    """One API-like decision built from completed candles only."""

    candle_index: int
    time: datetime
    action: Action
    confidence: int
    technical_score: int


@dataclass(frozen=True, slots=True)
class EAParameters:
    """Execution parameters matching the current unified EA defaults."""

    min_confidence: int = 75
    risk_percent: float = 0.5
    reward_risk_ratio: float = 2.0
    max_spread_points: float = 50.0
    max_spread_atr_ratio: float = 0.25
    max_daily_loss_percent: float = 1.5
    atr_period: int = 14
    atr_multiplier: float = 1.5
    min_stop_points: float = 150.0
    max_stop_points: float = 1200.0
    swing_lookback_bars: int = 30
    swing_left_bars: int = 2
    swing_right_bars: int = 2
    structure_buffer_points: float = 50.0
    use_anti_chase: bool = True
    max_extension_atr: float = 1.5
    pullback_zone_atr: float = 0.35
    pullback_max_bars: int = 4

    def validate(self) -> None:
        if not 0 <= self.min_confidence <= 100:
            raise ValueError("min_confidence must be between 0 and 100")
        if not 0 < self.risk_percent <= 0.5:
            raise ValueError("risk_percent must be > 0 and <= 0.5")
        if self.reward_risk_ratio <= 0:
            raise ValueError("reward_risk_ratio must be positive")
        if self.atr_period < 2 or self.atr_multiplier <= 0:
            raise ValueError("ATR settings are invalid")
        if self.min_stop_points <= 0 or self.max_stop_points < self.min_stop_points:
            raise ValueError("stop point settings are invalid")
        if self.swing_left_bars < 1 or self.swing_right_bars < 1:
            raise ValueError("swing confirmation bars must be positive")
        if self.swing_lookback_bars < self.swing_left_bars + self.swing_right_bars + 3:
            raise ValueError("swing_lookback_bars is too short")
        if self.max_extension_atr <= 0 or self.pullback_zone_atr < 0:
            raise ValueError("anti-chase settings are invalid")
        if self.pullback_max_bars < 1:
            raise ValueError("pullback_max_bars must be positive")


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    action: Action
    confidence: int
    technical_score: int
    entry_type: str
    stop_source: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float
    stop_points: float
    spread_points: float
    spread_to_atr: float
    risk_money: float
    pnl_r: float
    pnl_usd: float
    outcome: str
    holding_bars: int
    balance_after: float


@dataclass(frozen=True, slots=True)
class SimulationMetrics:
    start_balance: float
    end_balance: float
    net_usd: float
    return_percent: float
    trades: int
    buy_trades: int
    sell_trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    expectancy_r: float
    net_r: float
    max_drawdown_r: float
    max_drawdown_usd: float
    trading_days: int
    average_daily_pnl_usd: float
    daily_goal_usd: float
    daily_goal_progress_percent: float
    days_at_or_above_goal: int


@dataclass(slots=True)
class SimulationResult:
    trades: list[SimulatedTrade]
    metrics: SimulationMetrics
    daily_pnl: dict[date, float]
    blocked: Counter[str] = field(default_factory=Counter)
    decisions: int = 0
    directional_decisions: int = 0


@dataclass(slots=True)
class _OpenPosition:
    signal: HistoricalDecision
    entry_index: int
    entry_type: str
    stop_source: str
    entry_price: float
    stop_price: float
    target_price: float
    stop_points: float
    spread_points: float
    spread_to_atr: float
    risk_money: float


def _ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA series seeded with SMA, close to MT5 MODE_EMA behavior."""
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = alpha * values[index] + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _atr_series(candles: list[Candle], period: int) -> list[float | None]:
    """Wilder-smoothed ATR series used by the EA execution model."""
    result: list[float | None] = [None] * len(candles)
    if len(candles) < period:
        return result
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        if index == 0:
            true_range = candle.high - candle.low
        else:
            previous_close = candles[index - 1].close
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        true_ranges.append(true_range)
    seed = sum(true_ranges[:period]) / period
    result[period - 1] = seed
    previous = seed
    for index in range(period, len(candles)):
        previous = (previous * (period - 1) + true_ranges[index]) / period
        result[index] = previous
    return result


def generate_decisions(
    candles: list[Candle],
    *,
    symbol: str,
    point_size: float,
    lookback_bars: int = 100,
) -> list[HistoricalDecision]:
    """Replay the live Python signal engine using only completed historical bars."""
    if point_size <= 0:
        raise ValueError("point_size must be positive")
    if lookback_bars < 21:
        raise ValueError("lookback_bars must be at least 21")

    decisions: list[HistoricalDecision] = []
    for index in range(lookback_bars - 1, len(candles) - 1):
        window = candles[index - lookback_bars + 1 : index + 1]
        current = candles[index]
        spread_price = current.spread_points * point_size
        snapshot = MarketSnapshot(
            symbol=symbol,
            timeframe="M15",
            generated_at=current.time,
            bid=current.close,
            ask=current.close + spread_price,
            balance=1_000.0,
            equity=1_000.0,
            positions_total=0,
            opens=[item.open for item in window],
            highs=[item.high for item in window],
            lows=[item.low for item in window],
            closes=[item.close for item in window],
        )
        hint = build_hint(
            snapshot,
            news=[],
            max_risk_percent=0.5,
            tipranks_context=None,
            market_structure_context=None,
        )
        decisions.append(
            HistoricalDecision(
                candle_index=index,
                time=current.time,
                action=hint.action,
                confidence=hint.confidence,
                technical_score=hint.technical_score,
            )
        )
    return decisions


def _recent_confirmed_swing(
    candles: list[Candle],
    *,
    completed_index: int,
    action: Action,
    lookback_bars: int,
    left_bars: int,
    right_bars: int,
) -> float | None:
    """Mirror the EA's confirmed M15 swing search without looking ahead."""
    for shift in range(right_bars + 1, lookback_bars + 1):
        candidate_index = completed_index - (shift - 1)
        if candidate_index - left_bars < 0:
            break
        if candidate_index + right_bars > completed_index:
            continue

        candidate = (
            candles[candidate_index].low
            if action is Action.BUY
            else candles[candidate_index].high
        )
        confirmed = True
        for offset in range(1, left_bars + 1):
            older = (
                candles[candidate_index - offset].low
                if action is Action.BUY
                else candles[candidate_index - offset].high
            )
            if action is Action.BUY and candidate >= older:
                confirmed = False
                break
            if action is Action.SELL and candidate <= older:
                confirmed = False
                break
        for offset in range(1, right_bars + 1):
            if not confirmed:
                break
            newer = (
                candles[candidate_index + offset].low
                if action is Action.BUY
                else candles[candidate_index + offset].high
            )
            if action is Action.BUY and candidate > newer:
                confirmed = False
                break
            if action is Action.SELL and candidate < newer:
                confirmed = False
                break
        if confirmed:
            return candidate
    return None


def _build_plan(
    candles: list[Candle],
    *,
    entry_index: int,
    decision: HistoricalDecision,
    params: EAParameters,
    point_size: float,
    atr: float,
) -> tuple[float, float, float, float, str, float] | None:
    candle = candles[entry_index]
    spread_price = candle.spread_points * point_size
    entry_price = candle.open + spread_price if decision.action is Action.BUY else candle.open
    stop_points = max(params.min_stop_points, atr * params.atr_multiplier / point_size)
    stop_source = "ATR"

    swing = _recent_confirmed_swing(
        candles,
        completed_index=entry_index - 1,
        action=decision.action,
        lookback_bars=params.swing_lookback_bars,
        left_bars=params.swing_left_bars,
        right_bars=params.swing_right_bars,
    )
    if swing is not None:
        buffered = (
            swing - params.structure_buffer_points * point_size
            if decision.action is Action.BUY
            else swing + params.structure_buffer_points * point_size
        )
        structure_points = (
            (entry_price - buffered) / point_size
            if decision.action is Action.BUY
            else (buffered - entry_price) / point_size
        )
        if structure_points > stop_points and structure_points <= params.max_stop_points:
            stop_points = structure_points
            stop_source = "ATR+M15_SWING"

    if stop_points > params.max_stop_points:
        return None

    target_points = stop_points * params.reward_risk_ratio
    if decision.action is Action.BUY:
        stop_price = entry_price - stop_points * point_size
        target_price = entry_price + target_points * point_size
    else:
        stop_price = entry_price + stop_points * point_size
        target_price = entry_price - target_points * point_size

    spread_to_atr = spread_price / max(atr, 1e-12)
    return (
        entry_price,
        stop_price,
        target_price,
        stop_points,
        stop_source,
        spread_to_atr,
    )


def _position_exit(
    position: _OpenPosition,
    candle: Candle,
    *,
    point_size: float,
) -> tuple[str, float] | None:
    spread = candle.spread_points * point_size
    if position.signal.action is Action.BUY:
        stop_hit = candle.low <= position.stop_price
        target_hit = candle.high >= position.target_price
    else:
        ask_high = candle.high + spread
        ask_low = candle.low + spread
        stop_hit = ask_high >= position.stop_price
        target_hit = ask_low <= position.target_price

    if stop_hit:
        return "STOP", position.stop_price
    if target_hit:
        return "TARGET", position.target_price
    return None


def _pnl_r(position: _OpenPosition, exit_price: float, point_size: float) -> float:
    stop_distance = position.stop_points * point_size
    pnl_price = (
        exit_price - position.entry_price
        if position.signal.action is Action.BUY
        else position.entry_price - exit_price
    )
    return pnl_price / max(stop_distance, 1e-12)


def _calculate_metrics(
    trades: list[SimulatedTrade],
    *,
    start_balance: float,
    end_balance: float,
    trading_dates: list[date],
    daily_pnl: dict[date, float],
    daily_goal_usd: float,
) -> SimulationMetrics:
    wins = [trade for trade in trades if trade.pnl_r > 1e-12]
    losses = [trade for trade in trades if trade.pnl_r < -1e-12]
    gross_profit_r = sum(trade.pnl_r for trade in wins)
    gross_loss_r = abs(sum(trade.pnl_r for trade in losses))
    if gross_loss_r > 0:
        profit_factor = gross_profit_r / gross_loss_r
    elif gross_profit_r > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    cumulative_r = 0.0
    peak_r = 0.0
    max_drawdown_r = 0.0
    peak_balance = start_balance
    max_drawdown_usd = 0.0
    for trade in trades:
        cumulative_r += trade.pnl_r
        peak_r = max(peak_r, cumulative_r)
        max_drawdown_r = max(max_drawdown_r, peak_r - cumulative_r)
        peak_balance = max(peak_balance, trade.balance_after)
        max_drawdown_usd = max(max_drawdown_usd, peak_balance - trade.balance_after)

    net_r = sum(trade.pnl_r for trade in trades)
    trading_days = len(trading_dates)
    net_usd = end_balance - start_balance
    avg_daily = net_usd / trading_days if trading_days else 0.0
    progress = (
        max(0.0, avg_daily / daily_goal_usd * 100.0)
        if daily_goal_usd > 0
        else 0.0
    )
    return SimulationMetrics(
        start_balance=start_balance,
        end_balance=end_balance,
        net_usd=net_usd,
        return_percent=(net_usd / start_balance * 100.0) if start_balance else 0.0,
        trades=len(trades),
        buy_trades=sum(trade.action is Action.BUY for trade in trades),
        sell_trades=sum(trade.action is Action.SELL for trade in trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(trades) * 100.0) if trades else 0.0,
        profit_factor=profit_factor,
        expectancy_r=(net_r / len(trades)) if trades else 0.0,
        net_r=net_r,
        max_drawdown_r=max_drawdown_r,
        max_drawdown_usd=max_drawdown_usd,
        trading_days=trading_days,
        average_daily_pnl_usd=avg_daily,
        daily_goal_usd=daily_goal_usd,
        daily_goal_progress_percent=progress,
        days_at_or_above_goal=sum(
            daily_pnl.get(day, 0.0) >= daily_goal_usd for day in trading_dates
        ),
    )


def simulate_ea(
    candles: list[Candle],
    decisions: list[HistoricalDecision],
    *,
    params: EAParameters,
    point_size: float,
    initial_balance: float = 1_000.0,
    daily_goal_usd: float = 10.0,
    start_index: int | None = None,
    end_index: int | None = None,
) -> SimulationResult:
    """Replay the unified EA bar-by-bar on historical M15 data."""
    params.validate()
    if point_size <= 0 or initial_balance <= 0:
        raise ValueError("point_size and initial_balance must be positive")
    if not decisions:
        empty_metrics = _calculate_metrics(
            [],
            start_balance=initial_balance,
            end_balance=initial_balance,
            trading_dates=[],
            daily_pnl={},
            daily_goal_usd=daily_goal_usd,
        )
        return SimulationResult([], empty_metrics, {})

    decision_by_index = {item.candle_index: item for item in decisions}
    first_entry_index = min(decision_by_index) + 1
    last_entry_index = min(len(candles) - 1, max(decision_by_index) + 1)
    start = max(first_entry_index, start_index or first_entry_index)
    end = min(last_entry_index, end_index if end_index is not None else last_entry_index)
    if start > end:
        raise ValueError("simulation start is after end")

    closes = [candle.close for candle in candles]
    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    atr = _atr_series(candles, params.atr_period)

    balance = initial_balance
    day_start_balance = initial_balance
    current_day: date | None = None
    open_position: _OpenPosition | None = None
    pending_action: Action | None = None
    pending_start_index: int | None = None
    trades: list[SimulatedTrade] = []
    blocked: Counter[str] = Counter()
    daily_pnl: defaultdict[date, float] = defaultdict(float)
    trading_dates = sorted({candles[index].time.date() for index in range(start, end + 1)})

    def close_position(index: int, outcome: str, exit_price: float) -> None:
        nonlocal balance, open_position
        assert open_position is not None
        pnl_r = _pnl_r(open_position, exit_price, point_size)
        pnl_usd = pnl_r * open_position.risk_money
        balance += pnl_usd
        daily_pnl[candles[index].time.date()] += pnl_usd
        trades.append(
            SimulatedTrade(
                signal_time=open_position.signal.time,
                entry_time=candles[open_position.entry_index].time,
                exit_time=candles[index].time,
                action=open_position.signal.action,
                confidence=open_position.signal.confidence,
                technical_score=open_position.signal.technical_score,
                entry_type=open_position.entry_type,
                stop_source=open_position.stop_source,
                entry_price=open_position.entry_price,
                stop_price=open_position.stop_price,
                target_price=open_position.target_price,
                exit_price=exit_price,
                stop_points=open_position.stop_points,
                spread_points=open_position.spread_points,
                spread_to_atr=open_position.spread_to_atr,
                risk_money=open_position.risk_money,
                pnl_r=pnl_r,
                pnl_usd=pnl_usd,
                outcome=outcome,
                holding_bars=index - open_position.entry_index + 1,
                balance_after=balance,
            )
        )
        open_position = None

    for index in range(start, end + 1):
        candle = candles[index]
        if candle.time.date() != current_day:
            current_day = candle.time.date()
            day_start_balance = balance

        exited_carried_position = False
        if open_position is not None:
            resolved = _position_exit(open_position, candle, point_size=point_size)
            if resolved is not None:
                close_position(index, resolved[0], resolved[1])
                exited_carried_position = True
            else:
                continue

        # Conservative bar-level replay: if a carried trade exits during this
        # candle, do not assume a second intrabar entry after an unknown exit time.
        if exited_carried_position:
            blocked["same_bar_reentry_conservative"] += 1
            continue

        decision = decision_by_index.get(index - 1)
        if decision is None:
            blocked["no_decision"] += 1
            continue
        if decision.action not in {Action.BUY, Action.SELL}:
            blocked["wait"] += 1
            continue
        if decision.confidence < params.min_confidence:
            blocked["confidence"] += 1
            continue

        spread_points = candle.spread_points
        if params.max_spread_points > 0 and spread_points > params.max_spread_points:
            blocked["spread_points"] += 1
            continue

        completed = index - 1
        current_atr = atr[completed]
        current_ema9 = ema9[completed]
        current_ema21 = ema21[completed]
        if current_atr is None or current_atr <= 0:
            blocked["atr_unavailable"] += 1
            continue

        spread_to_atr = spread_points * point_size / current_atr
        if (
            params.max_spread_atr_ratio > 0
            and spread_to_atr > params.max_spread_atr_ratio
        ):
            blocked["spread_atr"] += 1
            continue

        day_drawdown = max(
            0.0,
            (day_start_balance - balance) / day_start_balance * 100.0,
        )
        if day_drawdown >= params.max_daily_loss_percent:
            blocked["daily_loss_limit"] += 1
            continue
        if day_drawdown + params.risk_percent > params.max_daily_loss_percent:
            blocked["daily_risk_budget"] += 1
            continue

        entry_type = "NORMAL"
        if params.use_anti_chase:
            if current_ema9 is None or current_ema21 is None:
                blocked["ema_unavailable"] += 1
                continue
            entry = (
                candle.open + spread_points * point_size
                if decision.action is Action.BUY
                else candle.open
            )
            extension_atr = (
                (entry - current_ema21) / current_atr
                if decision.action is Action.BUY
                else (current_ema21 - entry) / current_atr
            )

            if pending_action is not None and pending_action is not decision.action:
                pending_action = None
                pending_start_index = None

            if pending_action is None:
                if extension_atr > params.max_extension_atr:
                    pending_action = decision.action
                    pending_start_index = index
                    blocked["anti_chase_started"] += 1
                    continue
            else:
                assert pending_start_index is not None
                if index - pending_start_index > params.pullback_max_bars:
                    pending_action = None
                    pending_start_index = None
                    blocked["pullback_expired"] += 1
                    continue
                if extension_atr > params.max_extension_atr:
                    blocked["pullback_still_extended"] += 1
                    continue

                if decision.action is Action.BUY:
                    trend_aligned = current_ema9 > current_ema21
                    distance_atr = (entry - current_ema9) / current_atr
                    reclaimed = entry >= current_ema9 and entry >= current_ema21
                else:
                    trend_aligned = current_ema9 < current_ema21
                    distance_atr = (current_ema9 - entry) / current_atr
                    reclaimed = entry <= current_ema9 and entry <= current_ema21
                in_zone = 0.0 <= distance_atr <= params.pullback_zone_atr
                if not (trend_aligned and reclaimed and in_zone):
                    blocked["pullback_not_ready"] += 1
                    continue
                entry_type = "PULLBACK"
                pending_action = None
                pending_start_index = None

        plan = _build_plan(
            candles,
            entry_index=index,
            decision=decision,
            params=params,
            point_size=point_size,
            atr=current_atr,
        )
        if plan is None:
            blocked["stop_plan"] += 1
            continue
        entry_price, stop_price, target_price, stop_points, stop_source, ratio = plan
        risk_money = balance * params.risk_percent / 100.0
        open_position = _OpenPosition(
            signal=decision,
            entry_index=index,
            entry_type=entry_type,
            stop_source=stop_source,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            stop_points=stop_points,
            spread_points=spread_points,
            spread_to_atr=ratio,
            risk_money=risk_money,
        )

        # A new trade is entered at the bar open in this model, so its protective
        # orders may be touched later in the same bar.
        resolved = _position_exit(open_position, candle, point_size=point_size)
        if resolved is not None:
            close_position(index, resolved[0], resolved[1])

    if open_position is not None:
        final = candles[end]
        exit_price = (
            final.close
            if open_position.signal.action is Action.BUY
            else final.close + final.spread_points * point_size
        )
        close_position(end, "END_OF_WINDOW", exit_price)

    for day in trading_dates:
        daily_pnl.setdefault(day, 0.0)

    metrics = _calculate_metrics(
        trades,
        start_balance=initial_balance,
        end_balance=balance,
        trading_dates=trading_dates,
        daily_pnl=dict(daily_pnl),
        daily_goal_usd=daily_goal_usd,
    )
    return SimulationResult(
        trades=trades,
        metrics=metrics,
        daily_pnl=dict(sorted(daily_pnl.items())),
        blocked=blocked,
        decisions=sum(start - 1 <= item.candle_index <= end - 1 for item in decisions),
        directional_decisions=sum(
            start - 1 <= item.candle_index <= end - 1
            and item.action in {Action.BUY, Action.SELL}
            for item in decisions
        ),
    )


def _date_range_indices(
    candles: list[Candle],
    *,
    days: int | None,
    from_date: date | None,
    to_date: date | None,
) -> tuple[int | None, int | None]:
    dates = sorted({candle.time.date() for candle in candles})
    if not dates:
        return None, None
    selected_start = from_date
    selected_end = to_date
    if days is not None:
        if days < 1:
            raise ValueError("--days must be positive")
        selected = dates[-days:]
        selected_start, selected_end = selected[0], selected[-1]
    if selected_start is None and selected_end is None:
        return None, None

    first = next(
        (
            index
            for index, candle in enumerate(candles)
            if selected_start is None or candle.time.date() >= selected_start
        ),
        None,
    )
    last = next(
        (
            index
            for index in range(len(candles) - 1, -1, -1)
            if selected_end is None or candles[index].time.date() <= selected_end
        ),
        None,
    )
    return first, last


def write_trade_journal(path: Path, trades: list[SimulatedTrade]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "signal_time",
                "entry_time",
                "exit_time",
                "side",
                "confidence",
                "technical_score",
                "entry_type",
                "stop_source",
                "entry_price",
                "stop_price",
                "target_price",
                "exit_price",
                "stop_points",
                "spread_points",
                "spread_to_atr",
                "risk_money",
                "pnl_r",
                "pnl_usd",
                "outcome",
                "holding_bars",
                "balance_after",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade.signal_time.isoformat(sep=" "),
                    trade.entry_time.isoformat(sep=" "),
                    trade.exit_time.isoformat(sep=" "),
                    trade.action.value,
                    trade.confidence,
                    trade.technical_score,
                    trade.entry_type,
                    trade.stop_source,
                    f"{trade.entry_price:.5f}",
                    f"{trade.stop_price:.5f}",
                    f"{trade.target_price:.5f}",
                    f"{trade.exit_price:.5f}",
                    f"{trade.stop_points:.2f}",
                    f"{trade.spread_points:.2f}",
                    f"{trade.spread_to_atr:.6f}",
                    f"{trade.risk_money:.2f}",
                    f"{trade.pnl_r:.6f}",
                    f"{trade.pnl_usd:.2f}",
                    trade.outcome,
                    trade.holding_bars,
                    f"{trade.balance_after:.2f}",
                ]
            )


def write_daily_report(path: Path, result: SimulationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["broker_date", "realized_pnl_usd", "goal_usd", "goal_hit"])
        for day, pnl in result.daily_pnl.items():
            writer.writerow(
                [
                    day.isoformat(),
                    f"{pnl:.2f}",
                    f"{result.metrics.daily_goal_usd:.2f}",
                    "YES" if pnl >= result.metrics.daily_goal_usd else "NO",
                ]
            )


def render_report(
    result: SimulationResult,
    *,
    candles: list[Candle],
    params: EAParameters,
    start_index: int | None,
    end_index: int | None,
) -> str:
    metrics = result.metrics
    first = candles[start_index] if start_index is not None else candles[0]
    last = candles[end_index] if end_index is not None else candles[-1]
    pf = "inf" if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.3f}"
    lines = [
        "META TRADER AI - UNIFIED EA HISTORICAL SIMULATOR",
        "=" * 86,
        f"Range: {first.time} -> {last.time}",
        f"Decisions: {result.decisions} | Directional: {result.directional_decisions}",
        "Historical news/TipRanks: NOT reconstructed; MTF observer has no live confidence weight.",
        "OHLC ambiguity: same-bar STOP+TARGET resolves STOP first (conservative).",
        "Money model: continuous risk sizing; broker min/step lot constraints are not reconstructed.",
        "",
        "EA settings",
        f"  confidence>={params.min_confidence} | risk={params.risk_percent:.2f}% | RR={params.reward_risk_ratio:.2f}",
        f"  ATR x{params.atr_multiplier:.2f} | stop={params.min_stop_points:.0f}-{params.max_stop_points:.0f} pts",
        f"  spread<={params.max_spread_points:.0f} pts and <= {params.max_spread_atr_ratio:.2f} ATR",
        f"  daily loss={params.max_daily_loss_percent:.2f}% | anti-chase={'ON' if params.use_anti_chase else 'OFF'}",
        "",
        "Results",
        f"  Start balance: ${metrics.start_balance:,.2f}",
        f"  End balance:   ${metrics.end_balance:,.2f}",
        f"  Net P/L:       ${metrics.net_usd:+,.2f} ({metrics.return_percent:+.2f}%)",
        f"  Trades: {metrics.trades} | BUY {metrics.buy_trades} | SELL {metrics.sell_trades}",
        f"  Wins/Losses: {metrics.wins}/{metrics.losses} | Win rate {metrics.win_rate:.2f}%",
        f"  Profit factor: {pf}",
        f"  Expectancy: {metrics.expectancy_r:+.3f} R/trade | Net {metrics.net_r:+.2f} R",
        f"  Max DD: {metrics.max_drawdown_r:.2f} R | ${metrics.max_drawdown_usd:,.2f}",
        "",
        "Daily $10 goal",
        f"  Trading days: {metrics.trading_days}",
        f"  Average P/L/day: ${metrics.average_daily_pnl_usd:+.2f}",
        f"  Goal: ${metrics.daily_goal_usd:.2f}/day | Progress: {metrics.daily_goal_progress_percent:.1f}%",
        f"  Days at/above goal: {metrics.days_at_or_above_goal}/{metrics.trading_days}",
    ]
    if result.blocked:
        lines.extend(["", "Why entries were skipped"])
        for reason, count in result.blocked.most_common():
            lines.append(f"  {reason}: {count}")
    return "\n".join(lines) + "\n"


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--daily-goal", type=float, default=10.0)
    parser.add_argument("--days", type=int)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--confidence", type=int, default=75)
    parser.add_argument("--risk-percent", type=float, default=0.5)
    parser.add_argument("--rr", type=float, default=2.0)
    parser.add_argument("--max-spread-points", type=float, default=50.0)
    parser.add_argument("--max-spread-atr", type=float, default=0.25)
    parser.add_argument("--max-daily-loss", type=float, default=1.5)
    parser.add_argument("--atr-multiplier", type=float, default=1.5)
    parser.add_argument("--max-extension-atr", type=float, default=1.5)
    parser.add_argument("--pullback-zone-atr", type=float, default=0.35)
    parser.add_argument("--pullback-max-bars", type=int, default=4)
    parser.add_argument("--no-anti-chase", action="store_true")
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument("--output-prefix", type=Path, default=Path("data/ea_simulator"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.days is not None and (args.from_date or args.to_date):
        raise SystemExit("Use either --days or --from-date/--to-date, not both")

    candles = load_candles(args.csv_path)
    decisions = generate_decisions(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        lookback_bars=args.lookback_bars,
    )
    params = EAParameters(
        min_confidence=args.confidence,
        risk_percent=args.risk_percent,
        reward_risk_ratio=args.rr,
        max_spread_points=args.max_spread_points,
        max_spread_atr_ratio=args.max_spread_atr,
        max_daily_loss_percent=args.max_daily_loss,
        atr_multiplier=args.atr_multiplier,
        use_anti_chase=not args.no_anti_chase,
        max_extension_atr=args.max_extension_atr,
        pullback_zone_atr=args.pullback_zone_atr,
        pullback_max_bars=args.pullback_max_bars,
    )
    start_index, end_index = _date_range_indices(
        candles,
        days=args.days,
        from_date=_parse_date(args.from_date),
        to_date=_parse_date(args.to_date),
    )
    result = simulate_ea(
        candles,
        decisions,
        params=params,
        point_size=args.point_size,
        initial_balance=args.balance,
        daily_goal_usd=args.daily_goal,
        start_index=start_index,
        end_index=end_index,
    )

    report = render_report(
        result,
        candles=candles,
        params=params,
        start_index=start_index,
        end_index=end_index,
    )
    print(report, end="")

    prefix = args.output_prefix
    trade_path = prefix.with_name(prefix.name + "_trades.csv")
    daily_path = prefix.with_name(prefix.name + "_daily.csv")
    report_path = prefix.with_name(prefix.name + "_report.txt")
    write_trade_journal(trade_path, result.trades)
    write_daily_report(daily_path, result)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"Trade journal: {trade_path}")
    print(f"Daily report:  {daily_path}")
    print(f"Text report:   {report_path}")


if __name__ == "__main__":
    main()
