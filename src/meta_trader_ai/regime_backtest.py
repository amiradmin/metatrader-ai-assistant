"""Compare the current M15 baseline with a causal market-regime filter.

This module does not change the live signal engine.  It reuses historical
signals, labels each signal candle with a causal regime, and reports whether
trading only in an aligned trend improves the baseline.
"""

from __future__ import annotations

import argparse
import csv
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
from meta_trader_ai.models import Action
from meta_trader_ai.regime import RegimeState, TrendRegime, VolatilityRegime, classify_regime


@dataclass(frozen=True, slots=True)
class RegimeSignal:
    signal: HistoricalSignal
    regime: RegimeState


def label_signals(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    lookback_bars: int = 100,
) -> list[RegimeSignal]:
    """Attach a causal regime state to each already-generated signal."""
    labelled: list[RegimeSignal] = []
    for signal in signals:
        start = max(0, signal.candle_index - lookback_bars + 1)
        window = candles[start : signal.candle_index + 1]
        labelled.append(
            RegimeSignal(
                signal=signal,
                regime=classify_regime(window),
            )
        )
    return labelled


def is_trend_aligned(item: RegimeSignal) -> bool:
    """Allow BUY only in an uptrend and SELL only in a downtrend."""
    return (
        item.signal.action is Action.BUY
        and item.regime.trend is TrendRegime.TRENDING_UP
    ) or (
        item.signal.action is Action.SELL
        and item.regime.trend is TrendRegime.TRENDING_DOWN
    )


def _metrics_for(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    threshold: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> tuple[BacktestMetrics, list]:
    trades, candidates = simulate_trades(
        candles,
        signals,
        min_confidence=threshold,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    return (
        calculate_metrics(
            trades,
            threshold=threshold,
            candidate_signals=candidates,
        ),
        trades,
    )


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def print_comparison(
    baseline: BacktestMetrics,
    filtered: BacktestMetrics,
) -> None:
    """Print a compact baseline-vs-regime comparison."""
    print("\nREGIME FILTER COMPARISON")
    print("=" * 86)
    print(
        f"{'Mode':<22} {'Signals':>8} {'Trades':>7} {'Win%':>8} {'PF':>8} "
        f"{'Net R':>10} {'MaxDD R':>10}"
    )
    print("-" * 86)
    for name, metrics in (
        ("BASELINE", baseline),
        ("TREND_ALIGNED", filtered),
    ):
        print(
            f"{name:<22} {metrics.candidate_signals:>8} {metrics.trades:>7} "
            f"{metrics.win_rate:>7.2f}% {_fmt(metrics.profit_factor):>8} "
            f"{_fmt(metrics.net_r):>10} {_fmt(metrics.max_drawdown_r):>10}"
        )


def print_regime_distribution(labelled: list[RegimeSignal], *, threshold: int) -> None:
    """Show where eligible signals occur before max-one-position blocking."""
    eligible = [item for item in labelled if item.signal.confidence >= threshold]
    print(f"\nSIGNAL REGIME DISTRIBUTION @ confidence >= {threshold}")
    print("=" * 64)
    for regime in TrendRegime:
        count = sum(item.regime.trend is regime for item in eligible)
        pct = (count / len(eligible) * 100.0) if eligible else 0.0
        print(f"{regime.value:<20} {count:>7}  {pct:>6.2f}%")
    print()
    for regime in VolatilityRegime:
        count = sum(item.regime.volatility is regime for item in eligible)
        pct = (count / len(eligible) * 100.0) if eligible else 0.0
        print(f"{regime.value:<20} {count:>7}  {pct:>6.2f}%")


def write_regime_signals(
    path: Path,
    labelled: list[RegimeSignal],
    *,
    threshold: int,
) -> None:
    """Write signal-level regime diagnostics for later analysis."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "signal_time",
                "action",
                "confidence",
                "technical_score",
                "trend_regime",
                "volatility_regime",
                "efficiency_ratio",
                "net_move_atr",
                "atr",
                "volatility_ratio",
                "trend_aligned",
            ]
        )
        for item in labelled:
            if item.signal.confidence < threshold:
                continue
            writer.writerow(
                [
                    item.signal.time.isoformat(sep=" "),
                    item.signal.action.value,
                    item.signal.confidence,
                    item.signal.technical_score,
                    item.regime.trend.value,
                    item.regime.volatility.value,
                    f"{item.regime.efficiency_ratio:.6f}",
                    f"{item.regime.net_move_atr:.6f}",
                    f"{item.regime.atr:.6f}",
                    f"{item.regime.volatility_ratio:.6f}",
                    int(is_trend_aligned(item)),
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare baseline M15 trades with a causal trend-regime filter."
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
        default=Path("data/regime_backtest"),
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

    aligned_signals = [
        item.signal
        for item in labelled
        if is_trend_aligned(item)
    ]

    baseline, baseline_trades = _metrics_for(
        candles,
        signals,
        threshold=args.confidence,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
    )
    filtered, filtered_trades = _metrics_for(
        candles,
        aligned_signals,
        threshold=args.confidence,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
    )

    print(
        f"Candles: {len(candles):,} | Range: {candles[0].time} -> {candles[-1].time}"
    )
    print(
        "Regime classifier: 20-bar efficiency + ATR-normalized displacement; "
        "14-bar ATR vs preceding 50-bar volatility baseline."
    )
    print("Live signal engine: UNCHANGED")
    print_comparison(baseline, filtered)
    print_regime_distribution(labelled, threshold=args.confidence)

    baseline_path = args.output_prefix.with_name(
        f"{args.output_prefix.name}_baseline_conf{args.confidence}.csv"
    )
    filtered_path = args.output_prefix.with_name(
        f"{args.output_prefix.name}_trend_aligned_conf{args.confidence}.csv"
    )
    signals_path = args.output_prefix.with_name(
        f"{args.output_prefix.name}_signals_conf{args.confidence}.csv"
    )
    write_trades(baseline_path, baseline_trades)
    write_trades(filtered_path, filtered_trades)
    write_regime_signals(signals_path, labelled, threshold=args.confidence)
    print(f"\nBaseline journal: {baseline_path}")
    print(f"Trend-aligned journal: {filtered_path}")
    print(f"Signal regime diagnostics: {signals_path}")


if __name__ == "__main__":
    main()
