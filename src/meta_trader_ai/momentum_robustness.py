"""Backtest-only robustness grid for the promising M15 momentum band.

This module does not change the live engine.  It starts from the strongest
candidate population discovered so far (BUY, trend-aligned, LOW_VOLATILITY)
and checks whether the apparent edge around 1.5-2.0 ATR survives nearby
momentum bounds.  The goal is to detect a broad stable region, not select the
single best in-sample parameter pair.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from meta_trader_ai.backtest import BacktestMetrics, Candle, HistoricalSignal, load_candles
from meta_trader_ai.component_diagnostic import (
    ComponentSignal,
    _candidate_components,
    _metrics,
    _year_net,
)
from meta_trader_ai.walk_forward import _metrics_for_period, _period_bounds


@dataclass(frozen=True, slots=True)
class MomentumRow:
    lower: float | None
    upper: float | None
    metrics: BacktestMetrics
    year_2024_net_r: float
    year_2025_net_r: float
    year_2026_net_r: float
    positive_quarters: int
    total_quarters: int
    worst_quarter_r: float

    @property
    def all_years_positive(self) -> bool:
        return (
            self.year_2024_net_r > 0.0
            and self.year_2025_net_r > 0.0
            and self.year_2026_net_r > 0.0
        )


def _select(
    items: list[ComponentSignal],
    *,
    lower: float | None,
    upper: float | None,
) -> list[HistoricalSignal]:
    selected: list[HistoricalSignal] = []
    for item in items:
        value = item.momentum_4_atr
        if lower is not None and value < lower:
            continue
        if upper is not None and value >= upper:
            continue
        selected.append(item.signal)
    return selected


def _quarter_stats(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> tuple[int, int, float]:
    periods = _period_bounds(signals, "quarter")
    values: list[float] = []
    for _, start, end in periods:
        metrics = _metrics_for_period(
            candles,
            signals,
            start=start,
            end=end,
            threshold=70,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        values.append(metrics.net_r)
    if not values:
        return 0, 0, 0.0
    return sum(value > 0.0 for value in values), len(values), min(values)


def _evaluate_one(
    candles: list[Candle],
    items: list[ComponentSignal],
    *,
    lower: float | None,
    upper: float | None,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> MomentumRow:
    signals = _select(items, lower=lower, upper=upper)
    metrics = _metrics(
        candles,
        signals,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    years = {
        year: _year_net(
            candles,
            signals,
            year=year,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        for year in (2024, 2025, 2026)
    }
    positive_quarters, total_quarters, worst_quarter_r = _quarter_stats(
        candles,
        signals,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    return MomentumRow(
        lower=lower,
        upper=upper,
        metrics=metrics,
        year_2024_net_r=years[2024],
        year_2025_net_r=years[2025],
        year_2026_net_r=years[2026],
        positive_quarters=positive_quarters,
        total_quarters=total_quarters,
        worst_quarter_r=worst_quarter_r,
    )


def evaluate(
    candles: list[Candle],
    items: list[ComponentSignal],
    *,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> list[MomentumRow]:
    rows = [
        _evaluate_one(
            candles,
            items,
            lower=None,
            upper=None,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
    ]
    lowers = (1.0, 1.25, 1.5, 1.75)
    uppers: tuple[float | None, ...] = (1.75, 2.0, 2.25, 2.5, 3.0, None)
    for lower in lowers:
        for upper in uppers:
            if upper is not None and upper <= lower:
                continue
            rows.append(
                _evaluate_one(
                    candles,
                    items,
                    lower=lower,
                    upper=upper,
                    point_size=point_size,
                    stop_loss_points=stop_loss_points,
                    take_profit_points=take_profit_points,
                )
            )
    return rows


def _label(row: MomentumRow) -> str:
    if row.lower is None and row.upper is None:
        return "BASE_CANDIDATE"
    upper = "INF" if row.upper is None else f"{row.upper:g}"
    return f"{row.lower:g}-{upper}"


def print_report(rows: list[MomentumRow]) -> None:
    print("\nMOMENTUM ROBUSTNESS GRID")
    print("=" * 118)
    print("Population: TREND_AND_LOW_BUY_ONLY | Live engine: UNCHANGED")
    print(
        f"{'Momentum ATR':<16} {'Trades':>7} {'Win%':>8} {'PF':>7} {'NetR':>8} "
        f"{'DD':>7} {'2024':>7} {'2025':>7} {'2026':>7} {'PosQ':>7} {'WorstQ':>8}"
    )
    print("-" * 118)
    for row in rows:
        m = row.metrics
        print(
            f"{_label(row):<16} {m.trades:>7} {m.win_rate:>7.2f}% "
            f"{m.profit_factor:>7.2f} {m.net_r:>8.2f} {m.max_drawdown_r:>7.2f} "
            f"{row.year_2024_net_r:>7.2f} {row.year_2025_net_r:>7.2f} "
            f"{row.year_2026_net_r:>7.2f} "
            f"{row.positive_quarters:>2}/{row.total_quarters:<4} {row.worst_quarter_r:>8.2f}"
        )

    tested = [row for row in rows if row.lower is not None or row.upper is not None]
    all_years = [row for row in tested if row.all_years_positive]
    print(
        f"\nMomentum ranges positive in all three years: "
        f"{len(all_years)} / {len(tested)}"
    )
    if all_years:
        print("Stable candidates (all years > 0), sorted by NetR:")
        for row in sorted(all_years, key=lambda item: item.metrics.net_r, reverse=True):
            print(
                f"  {_label(row):<12} trades={row.metrics.trades:<4} "
                f"PF={row.metrics.profit_factor:.2f} NetR={row.metrics.net_r:+.2f} "
                f"DD={row.metrics.max_drawdown_r:.2f} "
                f"quarters={row.positive_quarters}/{row.total_quarters}"
            )


def write_csv(path: Path, rows: list[MomentumRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "momentum_lower",
                "momentum_upper",
                "candidate_signals",
                "trades",
                "win_rate",
                "profit_factor",
                "net_r",
                "max_drawdown_r",
                "year_2024_net_r",
                "year_2025_net_r",
                "year_2026_net_r",
                "all_years_positive",
                "positive_quarters",
                "total_quarters",
                "worst_quarter_r",
            ]
        )
        for row in rows:
            m = row.metrics
            writer.writerow(
                [
                    "" if row.lower is None else row.lower,
                    "" if row.upper is None else row.upper,
                    m.candidate_signals,
                    m.trades,
                    f"{m.win_rate:.6f}",
                    f"{m.profit_factor:.6f}",
                    f"{m.net_r:.6f}",
                    f"{m.max_drawdown_r:.6f}",
                    f"{row.year_2024_net_r:.6f}",
                    f"{row.year_2025_net_r:.6f}",
                    f"{row.year_2026_net_r:.6f}",
                    int(row.all_years_positive),
                    row.positive_quarters,
                    row.total_quarters,
                    f"{row.worst_quarter_r:.6f}",
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test nearby momentum/ATR bands for temporal robustness."
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/momentum_robustness.csv"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candles = load_candles(args.csv_path)
    items = _candidate_components(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        lookback_bars=args.lookback_bars,
    )
    rows = evaluate(
        candles,
        items,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
    )
    print(
        f"Candles: {len(candles):,} | Candidate regime BUY signals: {len(items):,}"
    )
    print_report(rows)
    write_csv(args.output, rows)
    print(f"\nMomentum robustness CSV: {args.output}")


if __name__ == "__main__":
    main()
