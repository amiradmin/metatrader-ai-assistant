"""Diagnose technical-score monotonicity for the strongest backtest-only candidate.

This module does not alter the live signal engine. It keeps the discovered
TREND_AND_LOW_BUY_ONLY regime filter fixed and asks whether the underlying
technical_score behaves more consistently than the current integer confidence
value.

Two views are reported:
1) THRESHOLD: trade candidate signals whose technical_score >= X.
2) BAND: trade candidate signals whose technical_score falls inside a fixed band.

All selected signals already passed the live engine's minimum confidence floor
when they were historically generated. Simulation therefore uses 70 only as the
backtester floor; the experiment itself is controlled by technical_score.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from meta_trader_ai.backtest import BacktestMetrics, Candle, HistoricalSignal, load_candles
from meta_trader_ai.confidence_diagnostic import (
    _candidate_signals,
    _full_metrics,
    _year_net,
)


@dataclass(frozen=True, slots=True)
class TechnicalScoreRow:
    mode: str
    label: str
    min_score: int
    max_score: int | None
    metrics: BacktestMetrics
    year_2024_net_r: float
    year_2025_net_r: float
    year_2026_net_r: float


def _evaluate_subset(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    mode: str,
    label: str,
    min_score: int,
    max_score: int | None,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> TechnicalScoreRow:
    metrics = _full_metrics(
        candles,
        signals,
        threshold=70,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    yearly = {
        year: _year_net(
            candles,
            signals,
            year=year,
            threshold=70,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        for year in (2024, 2025, 2026)
    }
    return TechnicalScoreRow(
        mode=mode,
        label=label,
        min_score=min_score,
        max_score=max_score,
        metrics=metrics,
        year_2024_net_r=yearly[2024],
        year_2025_net_r=yearly[2025],
        year_2026_net_r=yearly[2026],
    )


def evaluate(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    thresholds: list[int],
    bands: list[tuple[int, int]],
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> list[TechnicalScoreRow]:
    rows: list[TechnicalScoreRow] = []

    for score in thresholds:
        selected = [signal for signal in signals if signal.technical_score >= score]
        rows.append(
            _evaluate_subset(
                candles,
                selected,
                mode="THRESHOLD",
                label=f">={score}",
                min_score=score,
                max_score=None,
                point_size=point_size,
                stop_loss_points=stop_loss_points,
                take_profit_points=take_profit_points,
            )
        )

    for low, high in bands:
        selected = [
            signal
            for signal in signals
            if low <= signal.technical_score <= high
        ]
        rows.append(
            _evaluate_subset(
                candles,
                selected,
                mode="BAND",
                label=f"{low}-{high}",
                min_score=low,
                max_score=high,
                point_size=point_size,
                stop_loss_points=stop_loss_points,
                take_profit_points=take_profit_points,
            )
        )

    return rows


def print_report(rows: list[TechnicalScoreRow]) -> None:
    print("\nTECHNICAL SCORE MONOTONICITY DIAGNOSTIC")
    print("=" * 112)
    print("Strategy: TREND_AND_LOW_BUY_ONLY | Live engine: UNCHANGED")
    print(
        f"{'Mode':<10} {'Score':<9} {'Signals':>8} {'Trades':>7} {'Win%':>8} "
        f"{'PF':>7} {'NetR':>8} {'DD':>7} {'2024':>7} {'2025':>7} {'2026':>7}"
    )
    print("-" * 112)
    for row in sorted(
        rows,
        key=lambda item: (0 if item.mode == "THRESHOLD" else 1, item.min_score),
    ):
        m = row.metrics
        print(
            f"{row.mode:<10} {row.label:<9} {m.candidate_signals:>8} "
            f"{m.trades:>7} {m.win_rate:>7.2f}% {m.profit_factor:>7.2f} "
            f"{m.net_r:>8.2f} {m.max_drawdown_r:>7.2f} "
            f"{row.year_2024_net_r:>7.2f} {row.year_2025_net_r:>7.2f} "
            f"{row.year_2026_net_r:>7.2f}"
        )


def write_csv(path: Path, rows: list[TechnicalScoreRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "mode",
                "score_label",
                "min_score",
                "max_score",
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
        for row in sorted(
            rows,
            key=lambda item: (0 if item.mode == "THRESHOLD" else 1, item.min_score),
        ):
            m = row.metrics
            writer.writerow(
                [
                    row.mode,
                    row.label,
                    row.min_score,
                    "" if row.max_score is None else row.max_score,
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


def _parse_thresholds(value: str) -> list[int]:
    values = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not values or any(item < 30 or item > 100 for item in values):
        raise argparse.ArgumentTypeError("technical score thresholds must be 30..100")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose technical-score monotonicity inside the best regime filter."
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument(
        "--thresholds",
        type=_parse_thresholds,
        default=_parse_thresholds("30,35,40,45,50,55,60,65,70,75,80"),
    )
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/technical_score_diagnostic.csv"),
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
        thresholds=args.thresholds,
        bands=[(30, 39), (40, 49), (50, 59), (60, 69), (70, 79), (80, 100)],
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
