"""Compare the baseline M15 engine with the course-informed decision gates."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from meta_trader_ai.backtest import (
    BacktestMetrics,
    Candle,
    HistoricalSignal,
    calculate_metrics,
    generate_signals,
    load_candles,
    simulate_trades,
)
from meta_trader_ai.course_decision_tree import (
    CourseDecisionConfig,
    apply_course_decision_tree,
)
from meta_trader_ai.models import Action, MarketSnapshot
from meta_trader_ai.signals import MIN_ACTION_CONFIDENCE, build_hint


def generate_course_signals(
    candles: list[Candle],
    *,
    symbol: str,
    point_size: float,
    config: CourseDecisionConfig,
    lookback_bars: int = 100,
) -> tuple[list[HistoricalSignal], int]:
    """Generate historical signals after applying the course tree to each window."""
    if point_size <= 0:
        raise ValueError("point_size must be positive")
    if lookback_bars < 25:
        raise ValueError("lookback_bars must be at least 25")

    signals: list[HistoricalSignal] = []
    blocked = 0
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
            balance=10_000.0,
            equity=10_000.0,
            positions_total=0,
            opens=[candle.open for candle in window],
            highs=[candle.high for candle in window],
            lows=[candle.low for candle in window],
            closes=[candle.close for candle in window],
        )
        baseline = build_hint(
            snapshot,
            news=[],
            max_risk_percent=0.5,
            tipranks_context=None,
        )
        was_directional = baseline.action in {Action.BUY, Action.SELL}
        filtered = apply_course_decision_tree(snapshot, baseline, config)
        if was_directional and filtered.action is Action.WAIT:
            blocked += 1
        if filtered.action in {Action.BUY, Action.SELL}:
            signals.append(
                HistoricalSignal(
                    candle_index=index,
                    time=current.time,
                    action=filtered.action,
                    confidence=filtered.confidence,
                    technical_score=filtered.technical_score,
                )
            )
    return signals, blocked


def _metrics_for(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    threshold: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> BacktestMetrics:
    trades, candidates = simulate_trades(
        candles,
        signals,
        min_confidence=threshold,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    return calculate_metrics(
        trades,
        threshold=threshold,
        candidate_signals=candidates,
    )


def compare_course_tree(
    candles: list[Candle],
    *,
    symbol: str,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
    thresholds: list[int],
    lookback_bars: int = 100,
    config: CourseDecisionConfig | None = None,
) -> tuple[list[tuple[BacktestMetrics, BacktestMetrics]], int]:
    """Return baseline/course metrics for identical candles and execution rules."""
    config = config or CourseDecisionConfig()
    baseline_signals = generate_signals(
        candles,
        symbol=symbol,
        point_size=point_size,
        lookback_bars=lookback_bars,
    )
    course_signals, blocked = generate_course_signals(
        candles,
        symbol=symbol,
        point_size=point_size,
        config=config,
        lookback_bars=lookback_bars,
    )

    pairs: list[tuple[BacktestMetrics, BacktestMetrics]] = []
    for threshold in thresholds:
        baseline_metrics = _metrics_for(
            candles,
            baseline_signals,
            threshold=threshold,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        course_metrics = _metrics_for(
            candles,
            course_signals,
            threshold=threshold,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        pairs.append((baseline_metrics, course_metrics))
    return pairs, blocked


def _fmt(value: float) -> str:
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}"


def print_comparison(
    candles: list[Candle],
    pairs: list[tuple[BacktestMetrics, BacktestMetrics]],
    *,
    blocked: int,
) -> None:
    print("\nM15 BASELINE vs COURSE DECISION TREE")
    print("=" * 108)
    print(
        f"Candles: {len(candles):,} | Range: {candles[0].time} -> {candles[-1].time} | "
        f"raw directional candidates blocked by course tree: {blocked:,}"
    )
    print("Historical news/TipRanks/H1-H4 execution gates are not reconstructed here.")
    print("Both sides use the same next-bar entry and fixed SL/TP simulator.")
    print()
    print(
        f"{'Conf':>5} {'Mode':>9} {'Signals':>8} {'Trades':>7} {'Win%':>7} "
        f"{'PF':>7} {'NetR':>9} {'MaxDD':>9} {'DeltaR':>9}"
    )
    print("-" * 108)
    for baseline, course in pairs:
        delta_r = course.net_r - baseline.net_r
        print(
            f"{baseline.threshold:>5} {'BASE':>9} {baseline.candidate_signals:>8} "
            f"{baseline.trades:>7} {baseline.win_rate:>6.2f}% "
            f"{_fmt(baseline.profit_factor):>7} {baseline.net_r:>9.2f} "
            f"{baseline.max_drawdown_r:>9.2f} {'-':>9}"
        )
        print(
            f"{course.threshold:>5} {'COURSE':>9} {course.candidate_signals:>8} "
            f"{course.trades:>7} {course.win_rate:>6.2f}% "
            f"{_fmt(course.profit_factor):>7} {course.net_r:>9.2f} "
            f"{course.max_drawdown_r:>9.2f} {delta_r:>+9.2f}"
        )
        print("-" * 108)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare baseline M15 signals with the course decision-tree gates."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=Path("data/xauusd_m15_history.csv"),
    )
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument(
        "--thresholds",
        default="70,75,80,85,90",
        help="Comma-separated confidence thresholds, all >= 70.",
    )
    parser.add_argument("--range-lookback", type=int, default=24)
    parser.add_argument("--trend-lookback", type=int, default=20)
    parser.add_argument("--range-edge-fraction", type=float, default=0.20)
    parser.add_argument("--touch-tolerance-atr", type=float, default=0.20)
    parser.add_argument("--breakout-step-bars", type=int, default=5)
    parser.add_argument("--breakout-body-multiple", type=float, default=1.50)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    thresholds = sorted({int(value.strip()) for value in args.thresholds.split(",")})
    if not thresholds or any(value < MIN_ACTION_CONFIDENCE for value in thresholds):
        raise SystemExit(
            f"All thresholds must be >= {MIN_ACTION_CONFIDENCE}: {thresholds}"
        )

    candles = load_candles(args.csv_path)
    config = CourseDecisionConfig(
        range_lookback=args.range_lookback,
        trend_lookback=args.trend_lookback,
        range_edge_fraction=args.range_edge_fraction,
        touch_tolerance_atr=args.touch_tolerance_atr,
        breakout_step_bars=args.breakout_step_bars,
        breakout_body_multiple=args.breakout_body_multiple,
    )
    pairs, blocked = compare_course_tree(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
        thresholds=thresholds,
        lookback_bars=args.lookback_bars,
        config=config,
    )
    print_comparison(candles, pairs, blocked=blocked)


if __name__ == "__main__":
    main()
