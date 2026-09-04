import csv
from pathlib import Path

from meta_trader_ai.demo_kpi_report import (
    load_demo_journal,
    render_demo_report,
)


def test_demo_journal_loader_uses_valid_r_and_sums_money(tmp_path: Path) -> None:
    path = tmp_path / "demo_trade_journal.csv"
    fields = ["net_pnl", "pnl_r"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"net_pnl": "10.00", "pnl_r": "2.0"})
        writer.writerow({"net_pnl": "-5.00", "pnl_r": "-1.0"})
        writer.writerow({"net_pnl": "1.00", "pnl_r": ""})

    summary = load_demo_journal(path)
    assert summary.r_values == [2.0, -1.0]
    assert summary.net_pnl_money == 6.0
    assert summary.rows_with_missing_risk == 1


def test_demo_report_labels_early_real_forward_sample() -> None:
    summary_path_values = [2.0, -1.0, 2.0, -1.0]
    from meta_trader_ai.demo_kpi_report import DemoJournalSummary

    report = render_demo_report(
        DemoJournalSummary(summary_path_values, 12.5, 0),
        bootstrap_samples=1000,
    )

    assert "REAL DEMO FORWARD KPI" in report
    assert "Closed trades with valid initial-risk R: 4" in report
    assert "Expectancy:    +0.500 R/trade" in report
    assert "EARLY SAMPLE" in report
