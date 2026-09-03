"""Create a compact project KPI scorecard from historical and shadow-forward results.

Primary KPI: expectancy in R per closed trade. Supporting KPIs are win rate,
profit factor, net R, max drawdown, and forward sample size. The report keeps
historical evidence separate from truly unseen shadow-forward evidence.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class KpiMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    net_r: float
    max_drawdown_r: float
    expectancy_r: float


def metrics_from_r(values: list[float]) -> KpiMetrics:
    """Calculate core KPI metrics from chronologically ordered R outcomes."""
    wins = [value for value in values if value > 1e-12]
    losses = [value for value in values if value < -1e-12]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0.0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0.0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    trades = len(values)
    return KpiMetrics(
        trades=trades,
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / trades * 100.0) if trades else 0.0,
        profit_factor=profit_factor,
        net_r=sum(values),
        max_drawdown_r=max_drawdown,
        expectancy_r=(sum(values) / trades) if trades else 0.0,
    )


def load_backtest_journal(path: Path) -> KpiMetrics | None:
    """Load original backtest trade outcomes when the local journal exists."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", newline="") as handle:
        values = [float(row["pnl_r"]) for row in csv.DictReader(handle)]
    return metrics_from_r(values)


def load_frozen_candidate(
    path: Path,
    *,
    lower: float = 1.50,
    upper: float = 2.00,
) -> KpiMetrics | None:
    """Load the frozen momentum candidate from the robustness grid."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_low = row.get("momentum_lower", "").strip()
            raw_high = row.get("momentum_upper", "").strip()
            if not raw_low or not raw_high:
                continue
            if not math.isclose(float(raw_low), lower, abs_tol=1e-9):
                continue
            if not math.isclose(float(raw_high), upper, abs_tol=1e-9):
                continue
            trades = int(row["trades"])
            net_r = float(row["net_r"])
            return KpiMetrics(
                trades=trades,
                wins=round(float(row["win_rate"]) * trades / 100.0),
                losses=0,
                win_rate=float(row["win_rate"]),
                profit_factor=float(row["profit_factor"]),
                net_r=net_r,
                max_drawdown_r=float(row["max_drawdown_r"]),
                expectancy_r=(net_r / trades) if trades else 0.0,
            )
    return None


def load_shadow_trades(path: Path) -> KpiMetrics:
    """Use only CLOSED shadow trades; OPEN rows are not outcomes yet."""
    if not path.exists():
        return metrics_from_r([])
    values: list[float] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("event") != "CLOSE":
                continue
            raw = row.get("pnl_r", "").strip()
            if raw:
                values.append(float(raw))
    return metrics_from_r(values)


def evidence_stage(trades: int) -> str:
    if trades < 10:
        return "STARTUP (<10 closed trades)"
    if trades < 30:
        return "VERY EARLY (10-29)"
    if trades < 60:
        return "EARLY (30-59)"
    if trades < 100:
        return "MODERATE (60-99)"
    return "STRONGER SAMPLE (100+)"


def signal(value: float, *, green: float, yellow: float, higher_is_better: bool = True) -> str:
    if higher_is_better:
        if value >= green:
            return "GREEN"
        if value >= yellow:
            return "YELLOW"
        return "RED"
    if value <= green:
        return "GREEN"
    if value <= yellow:
        return "YELLOW"
    return "RED"


def _fmt(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.3f}"


def _metric_line(name: str, metrics: KpiMetrics) -> str:
    return (
        f"{name:<18} trades={metrics.trades:>4}  WR={metrics.win_rate:>6.2f}%  "
        f"PF={_fmt(metrics.profit_factor):>6}  E={metrics.expectancy_r:+.3f}R  "
        f"Net={metrics.net_r:+.2f}R  DD={metrics.max_drawdown_r:.2f}R"
    )


def render_report(
    *,
    baseline: KpiMetrics | None,
    candidate: KpiMetrics | None,
    forward: KpiMetrics,
    reward_risk: float,
) -> str:
    breakeven_wr = 100.0 / (1.0 + reward_risk)
    lines = [
        "META TRADER AI - PROJECT KPI SCORECARD",
        "=" * 90,
        f"Primary KPI: Forward Expectancy (R/trade) | RR={reward_risk:.2f}:1 | Break-even WR={breakeven_wr:.2f}%",
        "",
        "Historical reference (not forward proof):",
    ]
    lines.append(_metric_line("Original baseline", baseline) if baseline else "Original baseline  unavailable")
    lines.append(_metric_line("Frozen candidate", candidate) if candidate else "Frozen candidate   unavailable")

    if baseline is not None and candidate is not None:
        dd_reduction = (
            (baseline.max_drawdown_r - candidate.max_drawdown_r)
            / baseline.max_drawdown_r
            * 100.0
            if baseline.max_drawdown_r > 0.0
            else 0.0
        )
        lines.extend(
            [
                "",
                "Historical improvement vs original baseline:",
                f"  Win rate       {candidate.win_rate - baseline.win_rate:+.2f} percentage points",
                f"  Profit factor  {candidate.profit_factor - baseline.profit_factor:+.3f}",
                f"  Expectancy     {candidate.expectancy_r - baseline.expectancy_r:+.3f} R/trade",
                f"  Max drawdown   {dd_reduction:+.1f}% reduction",
            ]
        )

    wr_margin = forward.win_rate - breakeven_wr
    lines.extend(
        [
            "",
            "TRUE SHADOW FORWARD KPI:",
            _metric_line("Shadow forward", forward),
            f"Evidence stage: {evidence_stage(forward.trades)}",
        ]
    )

    if forward.trades == 0:
        lines.append("Status: COLLECT DATA - no closed forward trades yet.")
    else:
        lines.extend(
            [
                f"Expectancy light: {signal(forward.expectancy_r, green=0.15, yellow=0.0)}",
                f"Profit-factor light: {signal(forward.profit_factor, green=1.20, yellow=1.00)}",
                f"Win-rate margin: {wr_margin:+.2f} pp above/below break-even",
                f"Win-rate light: {signal(wr_margin, green=3.0, yellow=0.0)}",
                f"Drawdown light: {signal(forward.max_drawdown_r, green=10.0, yellow=20.0, higher_is_better=False)}",
            ]
        )
        if forward.trades < 30:
            lines.append("Interpretation: metrics are provisional; sample size is still too small for a strategy change.")
        elif forward.expectancy_r > 0.0 and forward.profit_factor >= 1.0:
            lines.append("Interpretation: forward edge is positive so far; keep parameters frozen and collect more data.")
        else:
            lines.append("Interpretation: forward evidence is not confirming the historical edge yet; do not optimize mid-test.")

    lines.extend(
        [
            "",
            "Rule: do not change the frozen strategy because of a few trades. Review the KPI trend, not one snapshot.",
        ]
    )
    return "\n".join(lines) + "\n"


def append_history(path: Path, metrics: KpiMetrics) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    fields = [
        "observed_at_utc",
        "closed_trades",
        "win_rate",
        "profit_factor",
        "expectancy_r",
        "net_r",
        "max_drawdown_r",
        "evidence_stage",
    ]
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "closed_trades": metrics.trades,
                "win_rate": f"{metrics.win_rate:.6f}",
                "profit_factor": "inf" if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.6f}",
                "expectancy_r": f"{metrics.expectancy_r:.6f}",
                "net_r": f"{metrics.net_r:.6f}",
                "max_drawdown_r": f"{metrics.max_drawdown_r:.6f}",
                "evidence_stage": evidence_stage(metrics.trades),
            }
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show project trading-quality KPIs.")
    parser.add_argument("--baseline", type=Path, default=Path("data/backtest_trades_conf75.csv"))
    parser.add_argument("--candidate", type=Path, default=Path("data/momentum_robustness.csv"))
    parser.add_argument("--shadow", type=Path, default=Path("data/shadow_trades.csv"))
    parser.add_argument("--history", type=Path, default=Path("data/kpi_history.csv"))
    parser.add_argument("--report", type=Path, default=Path("data/kpi_latest.txt"))
    parser.add_argument("--reward-risk", type=float, default=2.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.reward_risk <= 0.0:
        raise SystemExit("--reward-risk must be positive")

    baseline = load_backtest_journal(args.baseline)
    candidate = load_frozen_candidate(args.candidate)
    forward = load_shadow_trades(args.shadow)
    report = render_report(
        baseline=baseline,
        candidate=candidate,
        forward=forward,
        reward_risk=args.reward_risk,
    )
    print(report, end="")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    append_history(args.history, forward)
    print(f"Latest report: {args.report}")
    print(f"KPI history:   {args.history}")


if __name__ == "__main__":
    main()
