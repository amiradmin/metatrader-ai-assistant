"""Compare four controlled MetaTraderAI entry configurations on yesterday's data.

This is research-only historical replay. It never changes the live/demo EA and
never places MT5 orders. M15 builds directional signals; broker M1 data replays
anti-chase, pullback, entry, SL and TP.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from meta_trader_ai.backtest import load_candles
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


def _print_table(target_date: date, rows: list[dict[str, object]]) -> None:
    print("META TRADER AI - YESTERDAY ENTRY-GATE COMPARISON")
    print("=" * 104)
    print(f"Broker date: {target_date.isoformat()}")
    print("Risk, RR, spread guards and signal engine are frozen; only confidence/anti-chase settings vary.")
    print("Relaxed anti-chase = max extension 2.0 ATR, pullback zone 0.50 ATR, max wait 6 M15 bars.")
    print()
    print(
        f"{'ID':<3} {'CONFIG':<16} {'CAND':>5} {'TRD':>4} {'W/L':>7} "
        f"{'WR%':>7} {'NET$':>9} {'NET R':>8} {'E(R)':>8} {'DD R':>7}"
    )
    print("-" * 104)
    for row in rows:
        print(
            f"{row['scenario']:<3} {row['label']:<16} {int(row['candidates']):>5} "
            f"{int(row['trades']):>4} "
            f"{int(row['wins']):>2}/{int(row['losses']):<2} "
            f"{float(row['win_rate']):>6.1f}% "
            f"{float(row['net_usd']):>+9.2f} "
            f"{float(row['net_r']):>+8.2f} "
            f"{float(row['expectancy_r']):>+8.3f} "
            f"{float(row['max_drawdown_r']):>7.2f}"
        )
    print()
    print("Diagnostics")
    for row in rows:
        print(
            f"  {row['scenario']}: waiting={row['candidate_waiting']} "
            f"rejected={row['candidate_rejected']} | anti_chase={row['anti_chase_started']} "
            f"extended={row['pullback_still_extended']} not_ready={row['pullback_not_ready']} "
            f"expired={row['pullback_expired']}"
        )
    print()
    print("One day is diagnostic only; do not promote a configuration from this result alone.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Broker date YYYY-MM-DD; default is previous completed trading day.")
    parser.add_argument("--balance", type=float, default=1000.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    m15_path, m1_path, files_dir = _find_history_files()
    m15 = load_candles(m15_path)
    m1 = load_candles(m1_path)
    target_date = date.fromisoformat(args.date) if args.date else _previous_available_date(m1_path)
    start, end = _selected_indices(m1, target_date=target_date, days=None)

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

        prefix = files_dir / f"ea_compare_{scenario.code}"
        write_trade_journal(prefix.with_name(prefix.name + "_trades.csv"), result.trades)
        write_candidate_trace(prefix.with_name(prefix.name + "_candidates.csv"), traces)
        rows.append(_summary_row(scenario, result, traces))

    summary_path = files_dir / "ea_compare_summary.csv"
    _write_summary(summary_path, rows)
    _print_table(target_date, rows)

    print("\nREADY FOR MT5")
    print(f"Summary: {summary_path}")
    print("Scenario chart files:")
    for scenario in _scenarios():
        print(
            f"  {scenario.code}: ea_compare_{scenario.code}_trades.csv  |  "
            f"ea_compare_{scenario.code}_candidates.csv"
        )
    print("These files are historical paper simulations only; the live EA was not modified by this command.")


if __name__ == "__main__":
    main()
