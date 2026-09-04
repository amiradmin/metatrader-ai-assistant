"""One-command runner for yesterday's MetaTraderAI historical replay.

Finds the newest MT5 M15/M1 exports, selects the most recent completed broker
trading day before the latest available M1 day, runs the M15-signal/M1-execution
simulator, and writes the visualizer files directly into MQL5/Files.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from meta_trader_ai.backtest import load_candles
from meta_trader_ai.candidate_trace import build_candidate_trace, write_candidate_trace
from meta_trader_ai.ea_m1_simulator import (
    _selected_indices,
    render_m1_report,
    simulate_m15_signals_on_m1,
)
from meta_trader_ai.ea_simulator import (
    EAParameters,
    generate_decisions,
    write_daily_report,
    write_trade_journal,
)


def _newest(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.is_file()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _find_history_files() -> tuple[Path, Path, Path]:
    root = Path.home() / ".mt5"
    m1 = _newest(list(root.glob("**/MQL5/Files/xauusd_m1_history.csv")))
    if m1 is None:
        raise SystemExit(
            "M1 history not found. Run MT5 Scripts -> ExportSimulatorHistory first."
        )

    files_dir = m1.parent
    preferred_m15 = files_dir / "xauusd_m15_history.csv"
    if preferred_m15.is_file():
        m15 = preferred_m15
    else:
        m15 = _newest(list(root.glob("**/MQL5/Files/xauusd_m15_history.csv")))
    if m15 is None:
        raise SystemExit(
            "M15 history not found. Run MT5 Scripts -> ExportSimulatorHistory first."
        )
    return m15, m1, files_dir


def _previous_available_date(m1_path: Path):
    candles = load_candles(m1_path)
    dates = sorted({item.time.date() for item in candles})
    if len(dates) < 2:
        raise SystemExit("Not enough M1 history to determine the previous trading day.")
    return dates[-2]


def _copy_visualizer(files_dir: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source = repo_root / "mt5" / "SimulatorTradeVisualizer.mq5"
    scripts_dir = files_dir.parent / "Scripts"
    if source.is_file() and scripts_dir.is_dir():
        shutil.copy2(source, scripts_dir / source.name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="Broker date YYYY-MM-DD. Default: previous available trading day.",
    )
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--confidence", type=int, default=75)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    m15_path, m1_path, files_dir = _find_history_files()

    m15 = load_candles(m15_path)
    m1 = load_candles(m1_path)
    if args.date:
        from datetime import date

        target_date = date.fromisoformat(args.date)
    else:
        target_date = _previous_available_date(m1_path)

    start, end = _selected_indices(m1, target_date=target_date, days=None)
    decisions = generate_decisions(
        m15,
        symbol="XAUUSD_o",
        point_size=0.01,
        lookback_bars=100,
    )
    params = EAParameters(min_confidence=args.confidence)
    result = simulate_m15_signals_on_m1(
        m15,
        m1,
        decisions,
        params=params,
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
        params=params,
        point_size=0.01,
        start_index=start,
        end_index=end,
    )

    prefix = files_dir / "ea_simulator"
    trade_path = prefix.with_name(prefix.name + "_trades.csv")
    candidate_path = prefix.with_name(prefix.name + "_candidates.csv")
    daily_path = prefix.with_name(prefix.name + "_daily.csv")
    report_path = prefix.with_name(prefix.name + "_report.txt")

    write_trade_journal(trade_path, result.trades)
    write_candidate_trace(candidate_path, traces)
    write_daily_report(daily_path, result)
    report = render_m1_report(
        result,
        m1=m1,
        params=params,
        start_index=start,
        end_index=end,
    )
    report_path.write_text(report, encoding="utf-8")
    _copy_visualizer(files_dir)

    opened = sum(item.status == "OPENED" for item in traces)
    rejected = sum(item.status == "REJECTED" for item in traces)
    waiting = sum(item.status == "WAIT_PULLBACK" for item in traces)

    print(report, end="")
    print("\nCANDIDATE TRACE")
    print(
        f"Directional candidates: {len(traces)} | Opened: {opened} | "
        f"Rejected: {rejected} | Waiting pullback: {waiting}"
    )
    print("\nREADY FOR MT5")
    print(f"Date: {target_date.isoformat()} (broker date)")
    print(f"M15: {m15_path}")
    print(f"M1:  {m1_path}")
    print(f"Trades file:     {trade_path}")
    print(f"Candidates file: {candidate_path}")
    print("MT5 -> XAUUSD_o M15 -> Scripts -> SimulatorTradeVisualizer -> OK")
    print("Visualizer file names can stay at their defaults.")


if __name__ == "__main__":
    main()
