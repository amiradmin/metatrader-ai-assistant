"""Temporal stability validation for fixed M15 regime-filter strategies.

This module intentionally does not optimize parameters and does not alter the
live signal engine. It generates causal historical signals once, labels each
signal with the causal regime available at that time, and evaluates fixed
strategies over sequential calendar quarters and years.

Because the filters were discovered on the same historical sample, this is a
stability diagnostic rather than a pristine out-of-sample test. Future demo
forward-testing remains required before any live use.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
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
from meta_trader_ai.models import Action
from meta_trader_ai.regime import VolatilityRegime
from meta_trader_ai.regime_backtest import RegimeSignal, is_trend_aligned, label_signals


@dataclass(frozen=True, slots=True)
class PeriodResult:
    period: str
    strategy: str
    start: datetime
    end: datetime
    metrics: BacktestMetrics


def _strategy_signals(
    labelled: list[RegimeSignal],
    strategy: str,
) -> list[HistoricalSignal]:
    if strategy == "BASELINE":
        return [item.signal for item in labelled]
    if strategy == "LOW_VOL_ONLY":
        return [
            item.signal
            for item in labelled
            if item.regime.volatility is VolatilityRegime.LOW_VOLATILITY
        ]
    if strategy == "TREND_AND_LOW":
        return [
            item.signal
            for item in labelled
            if is_trend_aligned(item)
            and item.regime.volatility is VolatilityRegime.LOW_VOLATILITY
        ]
    if strategy == "TREND_AND_LOW_BUY_ONLY":
        return [
            item.signal
            for item in labelled
            if item.signal.action is Action.BUY
            and is_trend_aligned(item)
            and item.regime.volatility is VolatilityRegime.LOW_VOLATILITY
        ]
    raise ValueError(f"Unknown strategy: {strategy}")


def _quarter_key(value: datetime) -> tuple[int, int]:
    return value.year, ((value.month - 1) // 3) + 1


def _period_bounds(
    signals: list[HistoricalSignal],
    mode: str,
) -> list[tuple[str, datetime, datetime]]:
    if not signals:
        return []

    grouped: dict[tuple[int, int] | tuple[int], list[datetime]] = {}
    for signal in signals:
        if mode == "quarter":
            key: tuple[int, int] | tuple[int] = _quarter_key(signal.time)
        elif mode == "year":
            key = (signal.time.year,)
        else:
            raise ValueError("mode must be 'quarter' or 'year'")
        grouped.setdefault(key, []).append(signal.time)

    periods: list[tuple[str, datetime, datetime]] = []
    for key in sorted(grouped):
        values = grouped[key]
        if mode == "quarter":
            label = f"{key[0]}-Q{key[1]}"
        else:
            label = str(key[0])
        periods.append((label, min(values), max(values)))
    return periods


def _metrics_for_period(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    start: datetime,
    end: datetime,
    threshold: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> BacktestMetrics:
    period_signals = [signal for signal in signals if start <= signal.time <= end]
    trades, candidates = simulate_trades(
        candles,
        period_signals,
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


def evaluate_periods(
    candles: list[Candle],
    labelled: list[RegimeSignal],
    *,
    threshold: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
    mode: str,
) -> list[PeriodResult]:
    all_signals = [item.signal for item in labelled]
    periods = _period_bounds(all_signals, mode)
    strategies = (
        "BASELINE",
        "LOW_VOL_ONLY",
        "TREND_AND_LOW",
        "TREND_AND_LOW_BUY_ONLY",
    )

    results: list[PeriodResult] = []
    for strategy in strategies:
        selected = _strategy_signals(labelled, strategy)
        for label, start, end in periods:
            metrics = _metrics_for_period(
                candles,
                selected,
                start=start,
                end=end,
                threshold=threshold,
                point_size=point_size,
                stop_loss_points=stop_loss_points,
                take_profit_points=take_profit_points,
            )
            results.append(
                PeriodResult(
                    period=label,
                    strategy=strategy,
                    start=start,
                    end=end,
                    metrics=metrics,
                )
            )
    return results


def print_results(results: list[PeriodResult], *, mode: str) -> None:
    print(f"\nTEMPORAL STABILITY VALIDATION ({mode.upper()})")
    print("=" * 96)
    print(
        f"{'Period':<10} {'Strategy':<25} {'Trades':>7} {'Win%':>8} "
        f"{'PF':>8} {'Net R':>9} {'MaxDD':>9}"
    )
    print("-" * 96)
    current = None
    for item in sorted(results, key=lambda r: (r.period, r.strategy)):
        if current is not None and item.period != current:
            print("-" * 96)
        current = item.period
        metrics = item.metrics
        print(
            f"{item.period:<10} {item.strategy:<25} {metrics.trades:>7} "
            f"{metrics.win_rate:>7.2f}% {metrics.profit_factor:>8.2f} "
            f"{metrics.net_r:>9.2f} {metrics.max_drawdown_r:>9.2f}"
        )


def write_results(path: Path, results: list[PeriodResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "period",
                "strategy",
                "start",
                "end",
                "candidate_signals",
                "trades",
                "buy_trades",
                "sell_trades",
                "win_rate",
                "profit_factor",
                "net_r",
                "max_drawdown_r",
                "average_holding_bars",
            ]
        )
        for item in sorted(results, key=lambda r: (r.period, r.strategy)):
            metrics = item.metrics
            writer.writerow(
                [
                    item.period,
                    item.strategy,
                    item.start.isoformat(sep=" "),
                    item.end.isoformat(sep=" "),
                    metrics.candidate_signals,
                    metrics.trades,
                    metrics.buy_trades,
                    metrics.sell_trades,
                    f"{metrics.win_rate:.6f}",
                    f"{metrics.profit_factor:.6f}",
                    f"{metrics.net_r:.6f}",
                    f"{metrics.max_drawdown_r:.6f}",
                    f"{metrics.average_holding_bars:.6f}",
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate fixed M15 volatility/regime filters across time periods."
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
        default=Path("data/walk_forward"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candles = load_candles(args.csv_path)
    signals = generate_signals(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        lookback_bars=args.lookback_bars,
    )
    labelled = label_signals(
        candles,
        signals,
        lookback_bars=args.lookback_bars,
    )

    print(
        f"Candles: {len(candles):,} | Range: {candles[0].time} -> {candles[-1].time}"
    )
    print("Fixed parameters only; no per-period optimization.")
    print("Live signal engine: UNCHANGED")
    print(
        "Important: filters were discovered on this historical sample, so this is "
        "temporal stability analysis, not pristine out-of-sample proof."
    )

    quarter_results = evaluate_periods(
        candles,
        labelled,
        threshold=args.confidence,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
        mode="quarter",
    )
    year_results = evaluate_periods(
        candles,
        labelled,
        threshold=args.confidence,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
        mode="year",
    )

    print_results(quarter_results, mode="quarter")
    print_results(year_results, mode="year")

    quarter_path = args.output_prefix.with_name(
        f"{args.output_prefix.name}_quarterly_conf{args.confidence}.csv"
    )
    year_path = args.output_prefix.with_name(
        f"{args.output_prefix.name}_yearly_conf{args.confidence}.csv"
    )
    write_results(quarter_path, quarter_results)
    write_results(year_path, year_results)
    print(f"\nQuarterly results: {quarter_path}")
    print(f"Yearly results: {year_path}")


if __name__ == "__main__":
    main()
