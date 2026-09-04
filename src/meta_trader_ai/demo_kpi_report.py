"""Report KPIs from real trades executed by DemoAutoTrader on an MT5 demo account."""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

from meta_trader_ai.config import settings
from meta_trader_ai.kpi_report import (
    bootstrap_expectancy_ci,
    evidence_stage,
    metrics_from_r,
)


@dataclass(frozen=True, slots=True)
class DemoJournalSummary:
    r_values: list[float]
    net_pnl_money: float
    rows_with_missing_risk: int


def load_demo_journal(path: Path) -> DemoJournalSummary:
    """Load closed real-demo outcomes exported by DemoTradeJournal.mq5."""
    if not path.exists() or path.stat().st_size == 0:
        return DemoJournalSummary([], 0.0, 0)

    r_values: list[float] = []
    net_pnl_money = 0.0
    missing_risk = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_money = row.get("net_pnl", "").strip()
            if raw_money:
                net_pnl_money += float(raw_money)

            raw_r = row.get("pnl_r", "").strip()
            if raw_r:
                r_values.append(float(raw_r))
            else:
                missing_risk += 1

    return DemoJournalSummary(r_values, net_pnl_money, missing_risk)


def _fmt(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.3f}"


def render_demo_report(
    summary: DemoJournalSummary,
    *,
    bootstrap_confidence: float = 0.95,
    bootstrap_samples: int = 5000,
    bootstrap_seed: int = 260903,
) -> str:
    """Render a compact scorecard for actual demo-account executions."""
    metrics = metrics_from_r(summary.r_values)
    interval = bootstrap_expectancy_ci(
        summary.r_values,
        samples=bootstrap_samples,
        confidence=bootstrap_confidence,
        seed=bootstrap_seed,
    )

    lines = [
        "META TRADER AI - REAL DEMO FORWARD KPI",
        "=" * 78,
        "Source: closed positions actually executed by DemoAutoTrader on the demo account",
        "",
        f"Closed trades with valid initial-risk R: {metrics.trades}",
        f"Evidence stage: {evidence_stage(metrics.trades)}",
        f"Win rate:      {metrics.win_rate:.2f}%",
        f"Profit factor: {_fmt(metrics.profit_factor)}",
        f"Expectancy:    {metrics.expectancy_r:+.3f} R/trade",
        f"Net R:         {metrics.net_r:+.2f} R",
        f"Max drawdown:  {metrics.max_drawdown_r:.2f} R",
        f"Actual net P/L recorded by MT5: {summary.net_pnl_money:+.2f}",
    ]

    if summary.rows_with_missing_risk:
        lines.append(
            f"Warning: {summary.rows_with_missing_risk} closed trade(s) had no recoverable "
            "initial SL/risk and are excluded from R-based KPIs."
        )

    if interval is not None:
        lines.append(
            f"Bootstrap {bootstrap_confidence * 100:.0f}% expectancy CI: "
            f"[{interval[0]:+.3f}R, {interval[1]:+.3f}R]"
        )
        if interval[0] > 0.0:
            lines.append("Read: expectancy interval is above 0R so far; keep collecting data.")
        elif interval[1] < 0.0:
            lines.append("Read: expectancy interval is below 0R so far; forward edge is not confirmed.")
        else:
            lines.append("Read: expectancy interval crosses 0R; evidence is still uncertain.")
    elif metrics.trades:
        lines.append("Bootstrap CI: waiting for at least 5 closed demo trades.")

    if metrics.trades == 0:
        lines.append("Status: COLLECT DATA - no closed valid-R demo trades yet.")
    elif metrics.trades < 30:
        lines.append("Status: EARLY SAMPLE - do not optimize the strategy from a few outcomes.")
    elif metrics.expectancy_r > 0.0 and metrics.profit_factor >= 1.0:
        lines.append("Status: POSITIVE SO FAR - keep parameters frozen and grow the sample.")
    else:
        lines.append("Status: NOT CONFIRMED - collect/review evidence before changing risk upward.")

    lines.extend(
        [
            "",
            "Rule: demo profitability is evidence, not a guarantee of future or live-account profit.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--journal",
        type=Path,
        default=settings.demo_trade_journal_path,
        help="Path to MQL5/Files/demo_trade_journal.csv",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/demo_kpi_latest.txt"),
    )
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=260903)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 0.50 < args.bootstrap_confidence < 1.0:
        raise SystemExit("--bootstrap-confidence must be between 0.50 and 1.0")

    summary = load_demo_journal(args.journal)
    report = render_demo_report(
        summary,
        bootstrap_confidence=args.bootstrap_confidence,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
    )
    print(report, end="")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"Demo journal: {args.journal}")
    print(f"Latest report: {args.report}")


if __name__ == "__main__":
    main()
