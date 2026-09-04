"""Replay MetaTraderAI with M15 signals and real broker M1 execution data.

The signal engine only sees completed M15 candles. Once a signal is active, the
execution layer is replayed on M1 bars. Each M1 open acts as a one-minute
pseudo-tick for anti-chase/pullback checks; the M1 high/low is then used for
SL/TP resolution. This is closer to the live EA than the M15-only simulator,
but it is still not a tick-by-tick reconstruction.
"""

from __future__ import annotations

import argparse
import math
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from meta_trader_ai.backtest import Candle, load_candles
from meta_trader_ai.ea_simulator import (
    EAParameters,
    HistoricalDecision,
    SimulationResult,
    SimulatedTrade,
    _OpenPosition,
    _atr_series,
    _calculate_metrics,
    _ema_series,
    _pnl_r,
    _position_exit,
    _recent_confirmed_swing,
    generate_decisions,
    write_daily_report,
    write_trade_journal,
)
from meta_trader_ai.models import Action

M15 = timedelta(minutes=15)


def _selected_indices(
    candles: list[Candle],
    *,
    target_date: date | None,
    days: int | None,
) -> tuple[int, int]:
    if target_date is not None and days is not None:
        raise ValueError("Use either --date or --days, not both")
    if not candles:
        raise ValueError("M1 history is empty")

    dates = sorted({item.time.date() for item in candles})
    if target_date is not None:
        if target_date not in dates:
            raise ValueError(f"No M1 data exists for {target_date.isoformat()}")
        selected_dates = {target_date}
    elif days is not None:
        if days < 1:
            raise ValueError("--days must be positive")
        selected_dates = set(dates[-days:])
    else:
        selected_dates = set(dates)

    indices = [i for i, item in enumerate(candles) if item.time.date() in selected_dates]
    if not indices:
        raise ValueError("Selected M1 window is empty")
    return indices[0], indices[-1]


def _completed_m15_index(m15_times: list[datetime], when: datetime) -> int:
    """Return the latest M15 bar that was fully closed at ``when``."""
    return bisect_right(m15_times, when - M15) - 1


def _build_plan_at_m1_open(
    m15: list[Candle],
    *,
    completed_m15_index: int,
    m1_candle: Candle,
    decision: HistoricalDecision,
    params: EAParameters,
    point_size: float,
    atr: float,
) -> tuple[float, float, float, float, str, float] | None:
    spread_price = m1_candle.spread_points * point_size
    entry_price = (
        m1_candle.open + spread_price
        if decision.action is Action.BUY
        else m1_candle.open
    )
    stop_points = max(params.min_stop_points, atr * params.atr_multiplier / point_size)
    stop_source = "ATR"

    swing = _recent_confirmed_swing(
        m15,
        completed_index=completed_m15_index,
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
    return entry_price, stop_price, target_price, stop_points, stop_source, spread_to_atr


def simulate_m15_signals_on_m1(
    m15: list[Candle],
    m1: list[Candle],
    decisions: list[HistoricalDecision],
    *,
    params: EAParameters,
    point_size: float,
    initial_balance: float = 1_000.0,
    daily_goal_usd: float = 10.0,
    m1_start_index: int = 0,
    m1_end_index: int | None = None,
) -> SimulationResult:
    params.validate()
    if point_size <= 0 or initial_balance <= 0:
        raise ValueError("point_size and initial_balance must be positive")
    if not m15 or not m1:
        raise ValueError("Both M15 and M1 history are required")

    end = len(m1) - 1 if m1_end_index is None else min(m1_end_index, len(m1) - 1)
    start = max(0, m1_start_index)
    if start > end:
        raise ValueError("simulation start is after end")

    m15_times = [item.time for item in m15]
    latest_required_index = _completed_m15_index(m15_times, m1[end].time)
    if latest_required_index >= len(m15) - 1:
        raise ValueError(
            "M15 history is not fresh enough for the selected M1 window. "
            "Re-run HistoricalCsvExporter for XAUUSD_o/PERIOD_M15 and try again."
        )
    if latest_required_index < 100:
        raise ValueError("Not enough M15 lookback exists for the selected M1 window")

    decision_by_index = {item.candle_index: item for item in decisions}
    closes = [item.close for item in m15]
    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    atr = _atr_series(m15, params.atr_period)

    balance = initial_balance
    current_day: date | None = None
    day_start_balance = initial_balance
    open_position: _OpenPosition | None = None
    pending_action: Action | None = None
    pending_started_active_m15_index: int | None = None
    last_executed_active_m15_index: int | None = None
    trades: list[SimulatedTrade] = []
    blocked: Counter[str] = Counter()
    daily_pnl: defaultdict[date, float] = defaultdict(float)
    trading_dates = sorted({m1[i].time.date() for i in range(start, end + 1)})

    def close_position(index: int, outcome: str, exit_price: float) -> None:
        nonlocal balance, open_position
        assert open_position is not None
        pnl_r = _pnl_r(open_position, exit_price, point_size)
        pnl_usd = pnl_r * open_position.risk_money
        balance += pnl_usd
        daily_pnl[m1[index].time.date()] += pnl_usd
        trades.append(
            SimulatedTrade(
                signal_time=open_position.signal.time,
                entry_time=m1[open_position.entry_index].time,
                exit_time=m1[index].time,
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
        candle = m1[index]
        if candle.time.date() != current_day:
            current_day = candle.time.date()
            day_start_balance = balance

        exited_this_minute = False
        if open_position is not None:
            resolved = _position_exit(open_position, candle, point_size=point_size)
            if resolved is None:
                continue
            close_position(index, resolved[0], resolved[1])
            exited_this_minute = True

        if exited_this_minute:
            blocked["same_m1_reentry_conservative"] += 1
            continue

        completed = _completed_m15_index(m15_times, candle.time)
        if completed < 0:
            blocked["m15_not_ready"] += 1
            continue
        active_m15_index = completed + 1
        decision = decision_by_index.get(completed)
        if decision is None:
            blocked["no_decision"] += 1
            continue
        if decision.action not in {Action.BUY, Action.SELL}:
            blocked["wait"] += 1
            continue
        if decision.confidence < params.min_confidence:
            blocked["confidence"] += 1
            continue
        if last_executed_active_m15_index == active_m15_index:
            blocked["one_trade_per_m15_bar"] += 1
            continue

        current_atr = atr[completed]
        current_ema9 = ema9[completed]
        current_ema21 = ema21[completed]
        if current_atr is None or current_atr <= 0:
            blocked["atr_unavailable"] += 1
            continue

        spread_points = candle.spread_points
        if params.max_spread_points > 0 and spread_points > params.max_spread_points:
            blocked["spread_points"] += 1
            continue
        spread_to_atr = spread_points * point_size / current_atr
        if params.max_spread_atr_ratio > 0 and spread_to_atr > params.max_spread_atr_ratio:
            blocked["spread_atr"] += 1
            continue

        day_drawdown = max(0.0, (day_start_balance - balance) / day_start_balance * 100.0)
        if day_drawdown >= params.max_daily_loss_percent:
            blocked["daily_loss_limit"] += 1
            continue
        if day_drawdown + params.risk_percent > params.max_daily_loss_percent:
            blocked["daily_risk_budget"] += 1
            continue

        entry_type = "NORMAL_M1"
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
                pending_started_active_m15_index = None

            if pending_action is None:
                if extension_atr > params.max_extension_atr:
                    pending_action = decision.action
                    pending_started_active_m15_index = active_m15_index
                    blocked["anti_chase_started"] += 1
                    continue
            else:
                assert pending_started_active_m15_index is not None
                bars_waited = active_m15_index - pending_started_active_m15_index
                if bars_waited > params.pullback_max_bars:
                    pending_action = None
                    pending_started_active_m15_index = None
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
                entry_type = "PULLBACK_M1"
                pending_action = None
                pending_started_active_m15_index = None

        plan = _build_plan_at_m1_open(
            m15,
            completed_m15_index=completed,
            m1_candle=candle,
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
        last_executed_active_m15_index = active_m15_index

        resolved = _position_exit(open_position, candle, point_size=point_size)
        if resolved is not None:
            close_position(index, resolved[0], resolved[1])

    if open_position is not None:
        final = m1[end]
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
    decision_indices = {
        _completed_m15_index(m15_times, m1[i].time)
        for i in range(start, end + 1)
    }
    return SimulationResult(
        trades=trades,
        metrics=metrics,
        daily_pnl=dict(sorted(daily_pnl.items())),
        blocked=blocked,
        decisions=sum(index in decision_by_index for index in decision_indices),
        directional_decisions=sum(
            index in decision_by_index
            and decision_by_index[index].action in {Action.BUY, Action.SELL}
            for index in decision_indices
        ),
    )


def render_m1_report(
    result: SimulationResult,
    *,
    m1: list[Candle],
    params: EAParameters,
    start_index: int,
    end_index: int,
) -> str:
    metrics = result.metrics
    resolved = [trade for trade in result.trades if trade.outcome in {"TARGET", "STOP"}]
    wins = sum(trade.outcome == "TARGET" for trade in resolved)
    losses = sum(trade.outcome == "STOP" for trade in resolved)
    win_rate = wins / len(resolved) * 100.0 if resolved else 0.0
    pf = "inf" if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.3f}"
    lines = [
        "META TRADER AI - M15 SIGNAL / M1 EXECUTION SIMULATOR",
        "=" * 86,
        f"Range: {m1[start_index].time} -> {m1[end_index].time}",
        "Signal: completed M15 candles | Execution: M1 pseudo-ticks + M1 high/low",
        "Historical news/TipRanks are not reconstructed; this is not tick-perfect replay.",
        "Same-M1 STOP+TARGET ambiguity resolves STOP first (conservative).",
        "",
        "EA settings",
        f"  confidence>={params.min_confidence} | risk={params.risk_percent:.2f}% | RR={params.reward_risk_ratio:.2f}",
        f"  anti-chase={'ON' if params.use_anti_chase else 'OFF'} | max extension={params.max_extension_atr:.2f} ATR",
        f"  spread<={params.max_spread_points:.0f} pts and <= {params.max_spread_atr_ratio:.2f} ATR",
        f"  daily loss={params.max_daily_loss_percent:.2f}%",
        "",
        "Results",
        f"  M15 decisions: {result.decisions} | Directional: {result.directional_decisions}",
        f"  Trades: {metrics.trades} | BUY {metrics.buy_trades} | SELL {metrics.sell_trades}",
        f"  Resolved wins/losses: {wins}/{losses} | Win rate {win_rate:.2f}%",
        f"  End-of-window: {sum(t.outcome == 'END_OF_WINDOW' for t in result.trades)}",
        f"  Net P/L: ${metrics.net_usd:+,.2f} | End balance: ${metrics.end_balance:,.2f}",
        f"  Net: {metrics.net_r:+.2f} R | Expectancy: {metrics.expectancy_r:+.3f} R/trade",
        f"  Profit factor: {pf} | Max DD: {metrics.max_drawdown_r:.2f} R / ${metrics.max_drawdown_usd:,.2f}",
    ]
    if result.blocked:
        lines.extend(["", "Execution checks"])
        for reason, count in result.blocked.most_common():
            lines.append(f"  {reason}: {count}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("m15_csv", type=Path)
    parser.add_argument("--m1-csv", type=Path, required=True)
    parser.add_argument("--date", dest="target_date")
    parser.add_argument("--days", type=int)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--daily-goal", type=float, default=10.0)
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
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("data/ea_m1_simulator"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    target_date = date.fromisoformat(args.target_date) if args.target_date else None
    m15 = load_candles(args.m15_csv)
    m1 = load_candles(args.m1_csv)
    start, end = _selected_indices(m1, target_date=target_date, days=args.days)
    decisions = generate_decisions(
        m15,
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
    result = simulate_m15_signals_on_m1(
        m15,
        m1,
        decisions,
        params=params,
        point_size=args.point_size,
        initial_balance=args.balance,
        daily_goal_usd=args.daily_goal,
        m1_start_index=start,
        m1_end_index=end,
    )
    report = render_m1_report(result, m1=m1, params=params, start_index=start, end_index=end)
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
