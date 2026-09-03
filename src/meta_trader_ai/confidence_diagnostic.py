"""Diagnose whether the current integer confidence score is monotonic.

Backtest-only module. It does not change the live signal engine. The diagnostic
focuses on the strongest regime candidate discovered so far:
TREND_AND_LOW_BUY_ONLY.

It compares two questions for each confidence value:
1) THRESHOLD: trade every eligible signal with confidence >= X.
2) EXACT: trade only eligible signals whose confidence is exactly X.

If higher confidence is well calibrated, performance should generally improve
or at least remain stable as X rises. Large reversals are evidence that the
current confidence number is a heuristic score rather than a calibrated
probability of success.
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
)
from meta_trader_ai.models import Action
from meta_trader_ai.regime import VolatilityRegime
from meta_trader_ai.regime_backtest import is_trend_aligned, label_signals
from meta_trader_ai.walk_forward import _metrics_for_period, _period_bounds


@dataclass(frozen=True, slots=True)
class DiagnosticRow:
    mode: str
    confidence: int
    metrics: BacktestMetrics
    year_2024_net_r: float
    year_2025_net_r: float
    year_2026_net_r: float


def _candidate_signals(
    candles: list[Candle],
    *,
    symbol: str,
    point_size: float,
    lookback_bars: int,
) -> list[HistoricalSignal]:
    raw = generate_signals(
        candles,
        symbol=symbol,
        point_size=point_size,
        lookback_bars=lookback_bars,
    )
    labelled = label_signals(candles, raw, lookback_bars=lookback_bars)
    return [
        item.signal
        for item in labelled
        if item.signal.action is Action.BUY
        and is_trend_aligned(item)
        and item.regime.volatility is VolatilityRegime.LOW_VOLATILITY
    ]


def _full_metrics(
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


def _year_net(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    year: int,
    threshold: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> float:
    all_periods = _period_bounds(signals, "year")
    match = next((item for item in all_periods if item[0] == str(year)), None)
    if match is None:
        return 0.0
    _, start, end = match
    metrics = _metrics_for_period(
        candles,
        signals,
        start=start,
        end=end,
        threshold=threshold,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    return metrics.net_r


def evaluate(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    confidences: list[int],
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> list[DiagnosticRow]:
    rows: list[DiagnosticRow] = []
    for confidence in confidences:
        for mode in ("THRESHOLD", "EXACT"):
            if mode == "THRESHOLD":
                selected = signals
                simulation_floor = confidence
            else:
                selected = [signal for signal in signals if signal.confidence == confidence]
                # Exact signals are already selected, so use the engine floor.
                simulation_floor = 70

            metrics = _full_metrics(
                candles,
                selected,
                threshold=simulation_floor,
                point_size=point_size,
                stop_loss_points=stop_loss_points,
                take_profit_points=take_profit_points,
            )
            year_values = {}
            for year in (2024, 2025, 2026):
                year_values[year] = _year_net(
                    candles,
                    selected,
                    year=year,
                    threshold=simulation_floor,
                    point_size=point_size,
                    stop_loss_points=stop_loss_points,
                    take_profit_points=take_profit_points,
                )
            rows.append(
                DiagnosticRow(
                    mode=mode,
                    confidence=confidence,
                    metrics=metrics,
                    year_2024_net_r=year_values[2024],
                    year_2025_net_r=year_values[2025],
                    year_2026_net_r=year_values[2026],
                )
            )
    return rows


def print_report(rows: list[DiagnosticRow]) -> None:
    print("\nCONFIDENCE CALIBRATION DIAGNOSTIC")
    print("=" * 104)
    print("Strategy: TREND_AND_LOW_BUY_ONLY | Live engine: UNCHANGED")
    print(
        f"{'Mode':<10} {'Conf':>5} {'Signals':>8} {'Trades':>7} {'Win%':>8} "
        f"{'PF':>7} {'NetR':>8} {'DD':>7} {'2024':>7} {'2025':>7} {'2026':>7}"
    )
    print("-" * 104)
    for row in sorted(rows, key=lambda item: (item.confidence, item.mode)):
        m = row.metrics
        print(
            f"{row.mode:<10} {row.confidence:>5} {m.candidate_signals:>8} "
            f"{m.trades:>7} {m.win_rate:>7.2f}% {m.profit_factor:>7.2f} "
            f"{m.net_r:>8.2f} {m.max_drawdown_r:>7.2f} "
            f"{row.year_2024_net_r:>7.2f} {row.year_2025_net_r:>7.2f} "
            f"{row.year_2026_net_r:>7.2f}"
        )


def write_csv(path: Path, rows: list[DiagnosticRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "mode",
                "confidence",
                "candidate_signals",
                "trades",
                "win_rate",
                "profit_factor",
                "net_r",
                "max_drawdown_r",
                "year_2024_net_r",
                "year_2025_net_r",
                "year_2026_net_r",
            ]
        )
        for row in sorted(rows, key=lambda item: (item.confidence, item.mode)):
            m = row.metrics
            writer.writerow(
                [
                    row.mode,
                    row.confidence,
                    m.candidate_signals,
                    m.trades,
                    f"{m.win_rate:.6f}",
                    f"{m.profit_factor:.6f}",
                    f"{m.net_r:.6f}",
                    f"{m.max_drawdown_r:.6f}",
                    f"{row.year_2024_net_r:.6f}",
                    f"{row.year_2025_net_r:.6f}",
                    f"{row.year_2026_net_r:.6f}",
                ]
            )


def _parse_confidences(value: str) -> list[int]:
    values = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not values or any(item < 70 or item > 100 for item in values):
        raise argparse.ArgumentTypeError("confidences must be integers between 70 and 100")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose confidence-score calibration.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument(
        "--confidences",
        type=_parse_confidences,
        default=_parse_confidences("70,71,72,73,74,75,76,77,78,79,80"),
    )
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/confidence_diagnostic.csv"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candles = load_candles(args.csv_path)
    signals = _candidate_signals(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        lookback_bars=args.lookback_bars,
    )
    rows = evaluate(
        candles,
        signals,
        confidences=args.confidences,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
    )
    print(
        f"Candles: {len(candles):,} | Candidate regime signals: {len(signals):,}"
    )
    print_report(rows)
    write_csv(args.output, rows)
    print(f"\nDiagnostic CSV: {args.output}")


if __name__ == "__main__":
    main()
