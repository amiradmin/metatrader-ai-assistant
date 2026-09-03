"""Parameter-neighborhood robustness study for the strongest causal M15 candidate.

This module is research-only. It does not change the live signal engine or any
MT5 execution EA. The goal is to test whether the low-volatility BUY candidate
survives nearby confidence and SL/TP assumptions instead of relying on one
lucky parameter tuple.
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
from meta_trader_ai.regime_backtest import RegimeSignal, is_trend_aligned, label_signals


@dataclass(frozen=True, slots=True)
class RobustnessRow:
    strategy: str
    confidence: int
    stop_loss_points: float
    take_profit_points: float
    reward_risk: float
    trades: int
    win_rate: float
    profit_factor: float
    net_r: float
    max_drawdown_r: float
    positive_years: int
    flat_years: int
    negative_years: int
    worst_year_net_r: float
    best_year_net_r: float
    year_2024_net_r: float
    year_2025_net_r: float
    year_2026_net_r: float


def _allowed_signals(
    labelled: list[RegimeSignal],
    strategy: str,
) -> list[HistoricalSignal]:
    """Return the causal BUY-only candidate set for one research strategy."""
    if strategy == "LOW_VOL_BUY_ONLY":
        return [
            item.signal
            for item in labelled
            if item.signal.action is Action.BUY
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
    raise ValueError(f"Unknown robustness strategy: {strategy}")


def _metrics_for_trades(
    trades: list,
    *,
    threshold: int,
) -> BacktestMetrics:
    return calculate_metrics(
        trades,
        threshold=threshold,
        candidate_signals=len(trades),
    )


def _year_net_r(trades: list, year: int, threshold: int) -> float:
    subset = [trade for trade in trades if trade.entry_time.year == year]
    return _metrics_for_trades(subset, threshold=threshold).net_r


def run_grid(
    candles: list[Candle],
    labelled: list[RegimeSignal],
    *,
    point_size: float,
    confidences: list[int],
    stop_losses: list[float],
    reward_risks: list[float],
    strategies: list[str],
) -> list[RobustnessRow]:
    """Evaluate a small neighborhood of parameters, not a giant optimizer grid."""
    rows: list[RobustnessRow] = []
    years = (2024, 2025, 2026)

    for strategy in strategies:
        candidate_signals = _allowed_signals(labelled, strategy)
        for confidence in confidences:
            for stop_loss in stop_losses:
                for reward_risk in reward_risks:
                    take_profit = stop_loss * reward_risk
                    trades, candidates = simulate_trades(
                        candles,
                        candidate_signals,
                        min_confidence=confidence,
                        point_size=point_size,
                        stop_loss_points=stop_loss,
                        take_profit_points=take_profit,
                    )
                    metrics = calculate_metrics(
                        trades,
                        threshold=confidence,
                        candidate_signals=candidates,
                    )
                    year_values = {
                        year: _year_net_r(trades, year, confidence)
                        for year in years
                    }
                    values = list(year_values.values())
                    rows.append(
                        RobustnessRow(
                            strategy=strategy,
                            confidence=confidence,
                            stop_loss_points=stop_loss,
                            take_profit_points=take_profit,
                            reward_risk=reward_risk,
                            trades=metrics.trades,
                            win_rate=metrics.win_rate,
                            profit_factor=metrics.profit_factor,
                            net_r=metrics.net_r,
                            max_drawdown_r=metrics.max_drawdown_r,
                            positive_years=sum(value > 1e-12 for value in values),
                            flat_years=sum(abs(value) <= 1e-12 for value in values),
                            negative_years=sum(value < -1e-12 for value in values),
                            worst_year_net_r=min(values),
                            best_year_net_r=max(values),
                            year_2024_net_r=year_values[2024],
                            year_2025_net_r=year_values[2025],
                            year_2026_net_r=year_values[2026],
                        )
                    )
    return rows


def write_rows(path: Path, rows: list[RobustnessRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "strategy",
                "confidence",
                "stop_loss_points",
                "take_profit_points",
                "reward_risk",
                "trades",
                "win_rate",
                "profit_factor",
                "net_r",
                "max_drawdown_r",
                "positive_years",
                "flat_years",
                "negative_years",
                "worst_year_net_r",
                "best_year_net_r",
                "year_2024_net_r",
                "year_2025_net_r",
                "year_2026_net_r",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.strategy,
                    row.confidence,
                    f"{row.stop_loss_points:.2f}",
                    f"{row.take_profit_points:.2f}",
                    f"{row.reward_risk:.2f}",
                    row.trades,
                    f"{row.win_rate:.6f}",
                    f"{row.profit_factor:.6f}",
                    f"{row.net_r:.6f}",
                    f"{row.max_drawdown_r:.6f}",
                    row.positive_years,
                    row.flat_years,
                    row.negative_years,
                    f"{row.worst_year_net_r:.6f}",
                    f"{row.best_year_net_r:.6f}",
                    f"{row.year_2024_net_r:.6f}",
                    f"{row.year_2025_net_r:.6f}",
                    f"{row.year_2026_net_r:.6f}",
                ]
            )


def _parse_ints(raw: str) -> list[int]:
    return [int(value.strip()) for value in raw.split(",") if value.strip()]


def _parse_floats(raw: str) -> list[float]:
    return [float(value.strip()) for value in raw.split(",") if value.strip()]


def print_report(rows: list[RobustnessRow], *, top_n: int = 15) -> None:
    """Rank robust regions first, not merely the highest total Net R."""
    ranked = sorted(
        rows,
        key=lambda row: (
            row.positive_years,
            -row.negative_years,
            row.worst_year_net_r,
            row.profit_factor,
            row.net_r,
            -row.max_drawdown_r,
        ),
        reverse=True,
    )

    print("\nROBUSTNESS BACKTEST — LOW VOLATILITY BUY CANDIDATES")
    print("=" * 132)
    print(
        f"{'Strategy':<25} {'Conf':>5} {'SL':>6} {'TP':>7} {'RR':>5} "
        f"{'Trades':>7} {'Win%':>7} {'PF':>7} {'NetR':>8} {'DD':>7} "
        f"{'PosY':>5} {'WorstY':>8} {'2024':>7} {'2025':>7} {'2026':>7}"
    )
    print("-" * 132)
    for row in ranked[:top_n]:
        print(
            f"{row.strategy:<25} {row.confidence:>5} "
            f"{row.stop_loss_points:>6.0f} {row.take_profit_points:>7.0f} "
            f"{row.reward_risk:>5.2f} {row.trades:>7} {row.win_rate:>6.2f}% "
            f"{row.profit_factor:>7.2f} {row.net_r:>8.2f} "
            f"{row.max_drawdown_r:>7.2f} {row.positive_years:>5} "
            f"{row.worst_year_net_r:>8.2f} {row.year_2024_net_r:>7.2f} "
            f"{row.year_2025_net_r:>7.2f} {row.year_2026_net_r:>7.2f}"
        )

    all_three_positive = [row for row in rows if row.positive_years == 3]
    print(
        f"\nParameter combinations positive in all 3 years: "
        f"{len(all_three_positive)} / {len(rows)}"
    )
    print("Live signal engine: UNCHANGED")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Small parameter-neighborhood robustness test for low-vol BUY candidates."
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument("--confidences", default="70,72,75,78,80")
    parser.add_argument("--stop-losses", default="250,300,350")
    parser.add_argument("--reward-risks", default="1.5,2.0,2.5")
    parser.add_argument(
        "--strategies",
        default="LOW_VOL_BUY_ONLY,TREND_AND_LOW_BUY_ONLY",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/robustness_low_vol_buy.csv"),
    )
    parser.add_argument("--top", type=int, default=15)
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
    rows = run_grid(
        candles,
        labelled,
        point_size=args.point_size,
        confidences=_parse_ints(args.confidences),
        stop_losses=_parse_floats(args.stop_losses),
        reward_risks=_parse_floats(args.reward_risks),
        strategies=[
            value.strip()
            for value in args.strategies.split(",")
            if value.strip()
        ],
    )
    write_rows(args.output, rows)
    print(
        f"Candles: {len(candles):,} | Range: "
        f"{candles[0].time} -> {candles[-1].time}"
    )
    print_report(rows, top_n=args.top)
    print(f"Full grid CSV: {args.output}")


if __name__ == "__main__":
    main()
