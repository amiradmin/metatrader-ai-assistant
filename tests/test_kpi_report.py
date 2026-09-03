import csv
from pathlib import Path

from meta_trader_ai.kpi_report import (
    evidence_stage,
    load_shadow_trades,
    metrics_from_r,
)


def test_metrics_from_r_calculates_expectancy_pf_and_drawdown() -> None:
    metrics = metrics_from_r([2.0, -1.0, -1.0, 2.0])
    assert metrics.trades == 4
    assert metrics.wins == 2
    assert metrics.losses == 2
    assert metrics.win_rate == 50.0
    assert metrics.profit_factor == 2.0
    assert metrics.net_r == 2.0
    assert metrics.expectancy_r == 0.5
    assert metrics.max_drawdown_r == 2.0


def test_shadow_loader_uses_only_close_events(tmp_path: Path) -> None:
    path = tmp_path / "shadow.csv"
    fields = ["event", "pnl_r"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"event": "OPEN", "pnl_r": ""})
        writer.writerow({"event": "CLOSE", "pnl_r": "2.0"})
        writer.writerow({"event": "OPEN", "pnl_r": ""})
        writer.writerow({"event": "CLOSE", "pnl_r": "-1.0"})

    metrics = load_shadow_trades(path)
    assert metrics.trades == 2
    assert metrics.net_r == 1.0
    assert metrics.expectancy_r == 0.5


def test_evidence_stage_boundaries() -> None:
    assert evidence_stage(0).startswith("STARTUP")
    assert evidence_stage(10).startswith("VERY EARLY")
    assert evidence_stage(30).startswith("EARLY")
    assert evidence_stage(60).startswith("MODERATE")
    assert evidence_stage(100).startswith("STRONGER")
