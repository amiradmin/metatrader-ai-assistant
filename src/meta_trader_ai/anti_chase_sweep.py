"""Controlled Anti-Chase sweep using broker M15 signals and M1 execution.

Research-only: this command never edits the live EA and never places MT5 orders.
It reuses the existing causal signal engine and M1 execution simulator, sweeping
only Anti-Chase parameters while risk, RR, spread and daily-loss guards stay
frozen.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, replace
from pathlib import Path

from meta_trader_ai.backtest import Candle, load_candles
from meta_trader_ai.ea_m1_simulator import simulate_m15_signals_on_m1
from meta_trader_ai.ea_simulator import (
    EAParameters,
    SimulationResult,
    generate_decisions,
    write_daily_report,
    write_trade_journal,
)
from meta_trader_ai.simulate_yesterday import _find_history_files

DEFAULT_EXTENSIONS = (1.90, 2.00, 2.10, 2.25, 2.50, 2.75, 3.00)
DEFAULT_ZONES = (0.35, 0.50)
DEFAULT_WAITS = (4, 6)


@dataclass(frozen=True, slots=True)
class SweepConfig:
    code: str
    max_extension_atr: float
    pullback_zone_atr: float
    pullback_max_bars: int


def _parse_float_list(text: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in text.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("at least one value is required")
    if any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("values must be positive")
    return values


def _parse_int_list(text: str) -> tuple[int, ...]:
    values = tuple(int(item.strip()) for item in text.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("at least one value is required")
    if any(value < 1 for value in values):
        raise argparse.ArgumentTypeError("values must be >= 1")
    return values


def build_grid(
    extensions: tuple[float, ...] = DEFAULT_EXTENSIONS,
    zones: tuple[float, ...] = DEFAULT_ZONES,
    waits: tuple[int, ...] = DEFAULT_WAITS,
) -> list[SweepConfig]:
    configs: list[SweepConfig] = []
    for extension in extensions:
        for zone in zones:
            for wait in waits:
                configs.append(
                    SweepConfig(
                        code=f"E{extension:.2f}_Z{zone:.2f}_W{wait}",
                        max_extension_atr=extension,
                        pullback_zone_atr=zone,
                        pullback_max_bars=wait,
                    )
                )
    return configs


def _completed_window_indices(
    candles: list[Candle],
    *,
    days: int | None,
    include_latest: bool,
) -> tuple[int, int, list]:
    if not candles:
        raise ValueError("M1 history is empty")
    dates = sorted({item.time.date() for item in candles})
    usable = dates if include_latest else dates[:-1]
    if not usable:
        raise ValueError("Not enough M1 history after excluding the newest date")
    if days is not None:
        if days < 1:
            raise ValueError("--days must be positive")
        usable = usable[-days:]
    selected = set(usable)
    indices = [i for i, item in enumerate(candles) if item.time.date() in selected]
    if not indices:
        raise ValueError("Selected M1 window is empty")
    return indices[0], indices[-1], usable


def _trade_key(trade) -> tuple:
    return trade.signal_time, trade.action.value


def baseline_compare_counts(
    baseline: SimulationResult,
    scenario: SimulationResult,
) -> tuple[int, int, int]:
    """Return saved losses, missed winners and captured baseline winners.

    This is intentionally labelled as a baseline comparison, not a counterfactual
    proof: one-position-at-a-time execution makes trade paths dependent.
    """
    scenario_keys = {_trade_key(trade) for trade in scenario.trades}
    baseline_losses = {
        _trade_key(trade) for trade in baseline.trades if trade.outcome == "STOP"
    }
    baseline_winners = {
        _trade_key(trade) for trade in baseline.trades if trade.outcome == "TARGET"
    }
    saved_losses = len(baseline_losses - scenario_keys)
    missed_winners = len(baseline_winners - scenario_keys)
    captured_winners = len(baseline_winners & scenario_keys)
    return saved_losses, missed_winners, captured_winners


def _pf_value(value: float) -> float:
    return 999.0 if math.isinf(value) else value


def _summary_row(
    config: SweepConfig,
    result: SimulationResult,
    baseline: SimulationResult,
    *,
    min_trades: int,
    max_dd_r: float,
    min_pf: float,
) -> dict[str, object]:
    metrics = result.metrics
    saved, missed, captured = baseline_compare_counts(baseline, result)
    eligible = (
        metrics.trades >= min_trades
        and metrics.expectancy_r > 0.0
        and _pf_value(metrics.profit_factor) >= min_pf
        and metrics.max_drawdown_r <= max_dd_r
    )
    return {
        "code": config.code,
        "max_extension_atr": config.max_extension_atr,
        "pullback_zone_atr": config.pullback_zone_atr,
        "pullback_max_bars": config.pullback_max_bars,
        "trades": metrics.trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate": metrics.win_rate,
        "profit_factor": metrics.profit_factor,
        "expectancy_r": metrics.expectancy_r,
        "net_r": metrics.net_r,
        "net_usd": metrics.net_usd,
        "max_drawdown_r": metrics.max_drawdown_r,
        "trading_days": metrics.trading_days,
        "trades_per_day": metrics.trades / metrics.trading_days if metrics.trading_days else 0.0,
        "avg_daily_usd": metrics.average_daily_pnl_usd,
        "days_at_goal": metrics.days_at_or_above_goal,
        "saved_losses_vs_no_chase": saved,
        "missed_winners_vs_no_chase": missed,
        "captured_winners_vs_no_chase": captured,
        "eligible": eligible,
        "robust_neighbors_percent": 0.0,
        "anti_chase_started": result.blocked.get("anti_chase_started", 0),
        "pullback_still_extended": result.blocked.get("pullback_still_extended", 0),
        "pullback_not_ready": result.blocked.get("pullback_not_ready", 0),
        "pullback_expired": result.blocked.get("pullback_expired", 0),
        "stop_plan": result.blocked.get("stop_plan", 0),
    }


def _neighbor_robustness(rows: list[dict[str, object]]) -> None:
    """Measure how often immediate grid neighbours are also positive."""
    ext_values = sorted({float(row["max_extension_atr"]) for row in rows})
    zone_values = sorted({float(row["pullback_zone_atr"]) for row in rows})
    wait_values = sorted({int(row["pullback_max_bars"]) for row in rows})
    by_key = {
        (
            float(row["max_extension_atr"]),
            float(row["pullback_zone_atr"]),
            int(row["pullback_max_bars"]),
        ): row
        for row in rows
    }

    def adjacent(values, value):
        index = values.index(value)
        found = []
        if index > 0:
            found.append(values[index - 1])
        if index + 1 < len(values):
            found.append(values[index + 1])
        return found

    for row in rows:
        ext = float(row["max_extension_atr"])
        zone = float(row["pullback_zone_atr"])
        wait = int(row["pullback_max_bars"])
        keys = []
        keys.extend((item, zone, wait) for item in adjacent(ext_values, ext))
        keys.extend((ext, item, wait) for item in adjacent(zone_values, zone))
        keys.extend((ext, zone, item) for item in adjacent(wait_values, wait))
        neighbors = [by_key[key] for key in keys if key in by_key]
        if not neighbors:
            row["robust_neighbors_percent"] = 0.0
            continue
        positive = sum(
            float(item["expectancy_r"]) > 0.0 and _pf_value(float(item["profit_factor"])) > 1.0
            for item in neighbors
        )
        row["robust_neighbors_percent"] = positive / len(neighbors) * 100.0


def _rank_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        rows,
        key=lambda row: (
            bool(row["eligible"]),
            float(row["robust_neighbors_percent"]),
            float(row["expectancy_r"]),
            _pf_value(float(row["profit_factor"])),
            -float(row["max_drawdown_r"]),
            int(row["trades"]),
        ),
        reverse=True,
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _format_pf(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.2f}"


def _reference_line(label: str, result: SimulationResult) -> str:
    m = result.metrics
    return (
        f"{label:<18} TRD={m.trades:>3} WR={m.win_rate:>5.1f}% "
        f"PF={_format_pf(m.profit_factor):>5} E={m.expectancy_r:+.3f}R "
        f"NET={m.net_r:+.2f}R DD={m.max_drawdown_r:.2f}R "
        f"AVG={m.average_daily_pnl_usd:+.2f}$/day"
    )


def _render_report(
    *,
    rows: list[dict[str, object]],
    current: SimulationResult,
    no_chase: SimulationResult,
    m1: list[Candle],
    start: int,
    end: int,
    usable_dates: list,
    min_trades: int,
    min_pf: float,
    max_dd_r: float,
) -> str:
    ranked = _rank_rows(rows)
    lines = [
        "META TRADER AI - ANTI-CHASE M1 PARAMETER SWEEP",
        "=" * 118,
        f"Range: {m1[start].time} -> {m1[end].time} | completed trading dates={len(usable_dates)}",
        "Frozen: confidence=75 by default, risk=0.5%, RR=2.0, spread guards, daily-loss guard, signal engine.",
        "Varied only: MaxExtensionAtr, PullbackZoneAtr, PullbackMaxBars.",
        "Historical news/TipRanks are not reconstructed; M1 is pseudo-tick execution, not tick-perfect.",
        "",
        "REFERENCES",
        _reference_line("CURRENT E1.50", current),
        _reference_line("NO ANTI-CHASE", no_chase),
        "",
        f"Eligibility: trades>={min_trades}, PF>={min_pf:.2f}, expectancy>0, DD<={max_dd_r:.2f}R",
        "Neighbour robustness = share of immediate grid neighbours with positive expectancy and PF>1.",
        "Saved losses / missed winners are path-dependent comparisons versus no-anti-chase, not causal proof.",
        "",
        "TOP CONFIGURATIONS",
        f"{'#':>2} {'CONFIG':<18} {'TRD':>4} {'WR%':>6} {'PF':>6} {'E(R)':>8} {'NETR':>8} {'DDR':>7} {'$/D':>8} {'ROB%':>6} {'SAVE':>5} {'MISS':>5} {'OK':>3}",
        "-" * 118,
    ]
    for index, row in enumerate(ranked[:12], start=1):
        lines.append(
            f"{index:>2} {str(row['code']):<18} {int(row['trades']):>4} "
            f"{float(row['win_rate']):>5.1f}% {_format_pf(float(row['profit_factor'])):>6} "
            f"{float(row['expectancy_r']):>+8.3f} {float(row['net_r']):>+8.2f} "
            f"{float(row['max_drawdown_r']):>7.2f} {float(row['avg_daily_usd']):>+8.2f} "
            f"{float(row['robust_neighbors_percent']):>5.0f}% "
            f"{int(row['saved_losses_vs_no_chase']):>5} {int(row['missed_winners_vs_no_chase']):>5} "
            f"{'YES' if row['eligible'] else 'no':>3}"
        )
    eligible = [row for row in ranked if row["eligible"]]
    lines.extend(["", "INTERPRETATION"])
    if eligible:
        best = eligible[0]
        lines.append(
            "Research leader: " + str(best["code"]) +
            f" | E={float(best['expectancy_r']):+.3f}R | PF={_format_pf(float(best['profit_factor']))} | "
            f"DD={float(best['max_drawdown_r']):.2f}R | robustness={float(best['robust_neighbors_percent']):.0f}%"
        )
        lines.append("Do NOT auto-promote it to the live EA; validate on a different time window / walk-forward first.")
    else:
        lines.append("No configuration passed the minimum evidence gates. Keep the live EA unchanged.")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m15-csv", type=Path)
    parser.add_argument("--m1-csv", type=Path)
    parser.add_argument("--days", type=int, default=20, help="Completed broker trading dates; newest date is excluded by default.")
    parser.add_argument("--include-latest", action="store_true", help="Include newest M1 date even if it may be incomplete.")
    parser.add_argument("--extensions", type=_parse_float_list, default=DEFAULT_EXTENSIONS)
    parser.add_argument("--zones", type=_parse_float_list, default=DEFAULT_ZONES)
    parser.add_argument("--waits", type=_parse_int_list, default=DEFAULT_WAITS)
    parser.add_argument("--confidence", type=int, default=75)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--min-pf", type=float, default=1.15)
    parser.add_argument("--max-dd-r", type=float, default=6.0)
    parser.add_argument("--export-top", type=int, default=3)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.m15_csv and args.m1_csv:
        m15_path, m1_path = args.m15_csv, args.m1_csv
        output_dir = args.output_dir or Path("data")
    elif args.m15_csv or args.m1_csv:
        raise SystemExit("Pass both --m15-csv and --m1-csv, or neither for MT5 auto-detection.")
    else:
        m15_path, m1_path, files_dir = _find_history_files()
        output_dir = args.output_dir or files_dir

    m15 = load_candles(m15_path)
    m1 = load_candles(m1_path)
    start, end, usable_dates = _completed_window_indices(
        m1,
        days=args.days,
        include_latest=args.include_latest,
    )
    decisions = generate_decisions(
        m15,
        symbol="XAUUSD_o",
        point_size=0.01,
        lookback_bars=100,
    )

    base = EAParameters(min_confidence=args.confidence)
    current = simulate_m15_signals_on_m1(
        m15, m1, decisions,
        params=base,
        point_size=0.01,
        initial_balance=args.balance,
        daily_goal_usd=10.0,
        m1_start_index=start,
        m1_end_index=end,
    )
    no_chase_params = replace(base, use_anti_chase=False)
    no_chase = simulate_m15_signals_on_m1(
        m15, m1, decisions,
        params=no_chase_params,
        point_size=0.01,
        initial_balance=args.balance,
        daily_goal_usd=10.0,
        m1_start_index=start,
        m1_end_index=end,
    )

    rows: list[dict[str, object]] = []
    results: dict[str, SimulationResult] = {}
    configs = build_grid(args.extensions, args.zones, args.waits)
    for config in configs:
        params = replace(
            base,
            max_extension_atr=config.max_extension_atr,
            pullback_zone_atr=config.pullback_zone_atr,
            pullback_max_bars=config.pullback_max_bars,
        )
        result = simulate_m15_signals_on_m1(
            m15, m1, decisions,
            params=params,
            point_size=0.01,
            initial_balance=args.balance,
            daily_goal_usd=10.0,
            m1_start_index=start,
            m1_end_index=end,
        )
        results[config.code] = result
        rows.append(
            _summary_row(
                config,
                result,
                no_chase,
                min_trades=args.min_trades,
                max_dd_r=args.max_dd_r,
                min_pf=args.min_pf,
            )
        )

    _neighbor_robustness(rows)
    ranked = _rank_rows(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "anti_chase_sweep_summary.csv"
    report_path = output_dir / "anti_chase_sweep_report.txt"
    _write_csv(summary_path, ranked)
    report = _render_report(
        rows=rows,
        current=current,
        no_chase=no_chase,
        m1=m1,
        start=start,
        end=end,
        usable_dates=usable_dates,
        min_trades=args.min_trades,
        min_pf=args.min_pf,
        max_dd_r=args.max_dd_r,
    )
    report_path.write_text(report, encoding="utf-8")

    export_count = max(0, min(args.export_top, len(ranked)))
    for rank, row in enumerate(ranked[:export_count], start=1):
        result = results[str(row["code"])]
        prefix = output_dir / f"anti_chase_top{rank}_{row['code']}"
        write_trade_journal(prefix.with_name(prefix.name + "_trades.csv"), result.trades)
        write_daily_report(prefix.with_name(prefix.name + "_daily.csv"), result)

    print(report, end="")
    print(f"\nSummary CSV: {summary_path}")
    print(f"Text report: {report_path}")
    if export_count:
        print(f"Top {export_count} trade/daily journals were exported beside the summary.")
    print("LIVE EA: unchanged. This command is research-only.")


if __name__ == "__main__":
    main()
