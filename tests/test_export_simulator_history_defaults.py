from pathlib import Path


def test_export_simulator_history_requests_long_m1_window() -> None:
    source = (Path(__file__).resolve().parents[1] / "mt5" / "ExportSimulatorHistory.mq5").read_text(
        encoding="utf-8"
    )

    assert 'input int M1Bars = 300000;' in source
    assert 'input int M15Bars = 50000;' in source
    assert 'WARNING partial history' in source


def test_export_simulator_history_remains_read_only() -> None:
    source = (Path(__file__).resolve().parents[1] / "mt5" / "ExportSimulatorHistory.mq5").read_text(
        encoding="utf-8"
    )

    forbidden = ("OrderSend(", "OrderSendAsync(", "Trade.Buy(", "Trade.Sell(", "PositionClose(")
    for token in forbidden:
        assert token not in source
