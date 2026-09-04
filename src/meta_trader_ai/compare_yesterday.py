"""Compare four controlled MetaTraderAI entry configurations historically.

This is research-only historical replay. It never changes the live/demo EA and
never places MT5 orders. M15 builds directional signals; broker M1 data replays
anti-chase, pullback, entry, SL and TP.

By default it compares the previous completed broker trading day. Use
``--days 20`` to compare the most recent 20 *completed* trading days, excluding
the latest available M1 date so a partial current day cannot bias the result.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from meta_trader_ai.backtest import Candle, load_candles
from meta_trader_ai.candidate_trace import build_candidate_trace, write_candidate_trace
from meta_trader_ai.ea_m1_simulator import _selected_indices, simulate_m15_signals_on_m1
from meta_trader_ai.ea_simulator import (
    EAParameters,
    SimulationResult,
    generate_decisions,
    write_trade_journal,
)
from meta_trader_ai.simulate_yesterday import _find_history_files, _previous_available_date


@dataclass(frozen=True, slots=True)
class Scenario:
    code: str
    label: str
    params: EAParameters


def _scenarios() -> list[Scenario]:
    base = EAParameters()
    relaxed = replace(
        base,
        max_extension_atr=2.0,
        pullback_zone_atr=0.50,
        pullback_max_bars=6,
    )
    return [
        Scenario("A", "CURRENT C75", base),
        Scenario("B", "CURRENT C70", replace(base, min_confidence=70)),
        Scenario("C", "RELAXED C75", relaxed),
        Scenario("D", "RELAXED C70", replace(relaxed, min_confidence=70)),
    ]


def _completed_period_indices(candles: list[Candle], days: int) -> tuple[int, int, list[date]]:
    """Select the latest N completed broker dates, excluding the newest date."""
    if days < 1:
        raise ValueError("--days must be positive")
    dates = sorted({item.time.date() for item in candles})
    if len(dates) < days + 1:
        raise ValueError(
            f"Need at least {days + 1} M1 trading dates to test {days} completed days; "
            f"only {len(dates)} are available. Export more M1 history."
        )
    selected = dates[-(days + 1) : -1]
    selected_set = set(selected)
    indices = [index for index, candle in enumerate(candles) if candle.time.date() in selected_set]
    if not indices:
        raise ValueError("Selected completed M1 window is empty")
    return indices[0], indices[-1], selected


def _candidate_counts(traces) -> tuple[int, int, int, int]:
    opened = sum(item.status == "OPENED" for item in traces)
    rejected = sum(item.status == "REJECTED" for item in traces)
    waiting = sum(item.status == "WAIT_PULLBACK" for item in traces)
    return len(traces), opened, rejected, waiting


def _summary_row(
    scenario: Scenario,
    result: SimulationResult,
    traces,
) -> dict[str, object]:
    metrics = result.metrics
    candidates, opened, rejected, waiting = _candidate_counts(traces)
    trades_per_day = metrics.trades / metrics.trading_days if metrics.trading_days else 0.0
    return {
        "scenario": scenario.code,
        "label": scenario.label,
        "confidence": scenario.params.min_confidence,
        "max_extension_atr": scenario.params.max_extension_atr,
        "pullback_zone_atr": scenario.params.pullback_zone_atr,
        "pullback_max_bars": scenario.params.pullback_max_bars,
        "risk_percent": scenario.params.risk_percent,
        "rr": scenario.params.reward_risk_ratio,
        "candidates": candidates,
        "candidate_opened": opened,
        "candidate_rejected": rejected,
        "candidate_waiting": waiting,
        "trades": metrics.trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate": metrics.win_rate,
        "net_usd": metrics.net_usd,
        "net_r": metrics.net_r,
        "expectancy_r": metrics.expectancy_r,
        "profit_factor": metrics.profit_factor,
        "max_drawdown_r": metrics.max_drawdown_r,
        "trading_days": metrics.trading_days,
        "trades_per_day": trades_per_day,
        "average_daily_pnl_usd": metrics.average_daily_pnl_usd,
        "days_at_or_above_goal": metrics.days_at_or_above_goal,
        "anti_chase_started": result.blocked.get("anti_chase_started", 0),
        "pullback_still_extended": result.blocked.get("pullback_still_extended", 0),
        "pullback_not_ready": result.blocked.get("pullback_not_ready", 0),
        "pullback_expired": result.blocked.get("pullback_expired", 0),
    }


def _write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _print_table(range_text: str, rows: list[dict[str, object]]) -> None:
    print("META TRADER AI - ENTRY-GATE COMPARISON")
    print("=" * 126)
    print(f"Broker range: {range_text}")
    print("Risk, RR, spread guards and signal engine are frozen; only confidence/anti-chase settings vary.")
    print("Relaxed anti-chase = max extension 2.0 ATR, pullback zone 0.50 ATR, max wait 6 M15 bars.")
    print()
    print(
        f"{'ID':<3} {'CONFIG':<16} {'TRD':>4} {'W/L':>7} {'WR%':>7} {'NET$':>9} "
        f"{'NET R':>8} {'E(R)':>8} {'PF':>6} {'DD R':>7} {'T/D':>6} {'AVG$/D':>9} {'$10D':>6}"
    )
    print("-" * 126)
    for row in rows:
        print(
            f"{row['scenario']:<3} {row['label']:<16} "
            f"{int(row['trades']):>4} "
            f"{int(row['wins']):>2}/{int(row['losses']):<2} "
            f"{float(row['win_rate']):>6.1f}% "
            f"{float(row['net_usd']):>+9.2f} "
            f"{float(row['net_r']):>+8.2f} "
            f"{float(row['expectancy_r']):>+8.3f} "
            f"{float(row['profit_factor']):>6.2f} "
            f"{float(row['max_drawdown_r']):>7.2f} "
            f"{float(row['trades_per_day']):>6.2f} "
            f"{float(row['average_daily_pnl_usd']):>+9.2f} "
            f"{int(row['days_at_or_above_goal']):>6}"
        )
    print()
    print("Candidate / pullback diagnostics")
    for row in rows:
        print(
            f"  {row['scenario']}: candidates={row['candidates']} opened={row['candidate_opened']} "
            f"waiting={row['candidate_waiting']} rejected={row['candidate_rejected']} | "
            f"anti_chase={row['anti_chase_started']} extended={row['pullback_still_extended']} "
            f"not_ready={row['pullback_not_ready']} expired={row['pullback_expired']}"
        )
    print()
    print("Do not promote a configuration from one day; multi-day consistency matters more than peak profit.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--date", help="Broker date YYYY-MM-DD; default is previous completed trading day.")
    group.add_argument(
        "--days",
        type=int,
        help="Compare the latest N completed broker trading days, excluding the newest available date.",
    )
    parser.add_argument("--balance", type=float, default=1000.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    m15_path, m1_path, files_dir = _find_history_files()
    m15 = load_candles(m15_path)
    m1 = load_candles(m1_path)

    if args.days is not None:
        start, end, selected_dates = _completed_period_indices(m1, args.days)
        range_text = f"{selected_dates[0].isoformat()} -> {selected_dates[-1].isoformat()} ({len(selected_dates)} completed days)"
        output_suffix = f"_{args.days}d"
    else:
        target_date = date.fromisoformat(args.date) if args.date else _previous_available_date(m1_path)
        start, end = _selected_indices(m1, target_date=target_date, days=None)
        range_text = target_date.isoformat()
        output_suffix = ""

    decisions = generate_decisions(
        m15,
        symbol="XAUUSD_o",
        point_size=0.01,
        lookback_bars=100,
    )

    rows: list[dict[str, object]] = []
    for scenario in _scenarios():
        scenario.params.validate()
        result = simulate_m15_signals_on_m1(
            m15,
            m1,
            decisions,
            params=scenario.params,
            point_size=0.01,
            initial_balance=args.balance,
            daily_goal_usd=10.0,
            m1_start_index=start,
            m1_end_index=end,
        )
        traces = build_candidate_trace(
            m15,
            m1,
            decisions,
            result,
            params=scenario.params,
            point_size=0.01,
            start_index=start,
            end_index=end,
        )

        prefix = files_dir / f"ea_compare_{scenario.code}{output_suffix}"
        write_trade_journal(prefix.with_name(prefix.name + "_trades.csv"), result.trades)
        write_candidate_trace(prefix.with_name(prefix.name + "_candidates.csv"), traces)
        rows.append(_summary_row(scenario, result, traces))

    summary_path = files_dir / f"ea_compare_summary{output_suffix}.csv"
    _write_summary(summary_path, rows)
    _print_table(range_text, rows)

    print("\nREADY FOR MT5")
    print(f"Summary: {summary_path}")
    print("Scenario chart files:")
    for scenario in _scenarios():
        print(
            f"  {scenario.code}: ea_compare_{scenario.code}{output_suffix}_trades.csv  |  "
            f"ea_compare_{scenario.code}{output_suffix}_candidates.csv"
        )
    print("These files are historical paper simulations only; the live EA was not modified by this command.")


if __name__ == "__main__":
    main()
