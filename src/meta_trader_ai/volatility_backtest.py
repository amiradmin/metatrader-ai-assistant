"""Compare volatility-regime filters without changing the live trading engine."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from meta_trader_ai.backtest import (
    BacktestMetrics,
    Candle,
    HistoricalSignal,
    calculate_metrics,
    generate_signals,
    load_candles,
    simulate_trades,
    write_trades,
)
from meta_trader_ai.regime import VolatilityRegime
from meta_trader_ai.regime_backtest import RegimeSignal, is_trend_aligned, label_signals


@dataclass(frozen=True, slots=True)
class FilterResult:
    """One causal regime-filter experiment and its simulated trades."""

    name: str
    metrics: BacktestMetrics
    trades: list


def filter_labelled_signals(
    labelled: list[RegimeSignal],
    mode: str,
) -> list[HistoricalSignal]:
    """Return signals allowed by one causal volatility/trend experiment."""
    if mode == "BASELINE":
        return [item.signal for item in labelled]
    if mode == "TREND_ALIGNED":
        return [item.signal for item in labelled if is_trend_aligned(item)]
    if mode == "NO_HIGH_VOL":
        return [
            item.signal
            for item in labelled
            if item.regime.volatility is not VolatilityRegime.HIGH_VOLATILITY
        ]
    if mode == "LOW_VOL_ONLY":
        return [
            item.signal
            for item in labelled
            if item.regime.volatility is VolatilityRegime.LOW_VOLATILITY
        ]
    if mode == "TREND_AND_NO_HIGH":
        return [
            item.signal
            for item in labelled
            if is_trend_aligned(item)
            and item.regime.volatility is not VolatilityRegime.HIGH_VOLATILITY
        ]
    if mode == "TREND_AND_LOW":
        return [
            item.signal
            for item in labelled
            if is_trend_aligned(item)
            and item.regime.volatility is VolatilityRegime.LOW_VOLATILITY
        ]
    raise ValueError(f"Unknown filter mode: {mode}")


def _run_one(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    name: str,
    threshold: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> FilterResult:
    trades, candidates = simulate_trades(
        candles,
        signals,
        min_confidence=threshold,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    metrics = calculate_metrics(
        trades,
        threshold=threshold,
        candidate_signals=candidates,
    )
    return FilterResult(name=name, metrics=metrics, trades=trades)


def print_report(results: list[FilterResult]) -> None:
    """Print all volatility experiments beside the untouched baseline."""
    print("\nVOLATILITY REGIME FILTER COMPARISON")
    print("=" * 96)
    print(
        f"{'Mode':<22} {'Signals':>8} {'Trades':>7} {'Win%':>8} {'PF':>8} "
        f"{'Net R':>10} {'MaxDD R':>10} {'Avg R':>9}"
    )
    print("-" * 96)
    for result in results:
        metrics = result.metrics
        average_r = metrics.net_r / metrics.trades if metrics.trades else 0.0
        print(
            f"{result.name:<22} {metrics.candidate_signals:>8} {metrics.trades:>7} "
            f"{metrics.win_rate:>7.2f}% {metrics.profit_factor:>8.2f} "
            f"{metrics.net_r:>10.2f} {metrics.max_drawdown_r:>10.2f} "
            f"{average_r:>9.3f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare baseline, trend, and causal volatility-regime filters on M15."
        )
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument("--confidence", type=int, default=75)
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("data/volatility_backtest"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candles = load_candles(args.csv_path)
    raw_signals = generate_signals(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        lookback_bars=args.lookback_bars,
    )
    labelled = label_signals(
        candles,
        raw_signals,
        lookback_bars=args.lookback_bars,
    )

    modes = (
        "BASELINE",
        "TREND_ALIGNED",
        "NO_HIGH_VOL",
        "LOW_VOL_ONLY",
        "TREND_AND_NO_HIGH",
        "TREND_AND_LOW",
    )
    results: list[FilterResult] = []
    for mode in modes:
        allowed = filter_labelled_signals(labelled, mode)
        results.append(
            _run_one(
                candles,
                allowed,
                name=mode,
                threshold=args.confidence,
                point_size=args.point_size,
                stop_loss_points=args.sl_points,
                take_profit_points=args.tp_points,
            )
        )

    print(
        f"Candles: {len(candles):,} | Range: {candles[0].time} -> {candles[-1].time}"
    )
    print(
        "All regime labels are causal; only completed candles available at signal time "
        "are used."
    )
    print("Live signal engine: UNCHANGED")
    print_report(results)

    for result in results:
        suffix = result.name.lower()
        path = args.output_prefix.with_name(
            f"{args.output_prefix.name}_{suffix}_conf{args.confidence}.csv"
        )
        write_trades(path, result.trades)
        print(f"{result.name:<22} journal: {path}")


if __name__ == "__main__":
    main()
