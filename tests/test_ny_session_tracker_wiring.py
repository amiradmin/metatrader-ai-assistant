from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ny_tracker_is_analytics_only() -> None:
    tracker = (ROOT / "mt5" / "NYSessionTracker.mqh").read_text(encoding="utf-8")
    forbidden = (
        "Trade.Buy(",
        "Trade.Sell(",
        "Trade.PositionClose(",
        "OrderSend(",
        "OrderSendAsync(",
    )
    for token in forbidden:
        assert token not in tracker


def test_unified_ea_wrapper_wires_core_and_ny_tracker() -> None:
    wrapper = (ROOT / "mt5" / "MetaTraderAI.mq5").read_text(encoding="utf-8")
    assert '#include "MetaTraderAI_Core.mqh"' in wrapper
    assert '#include "NYSessionTracker.mqh"' in wrapper
    assert "NYTrackerInit();" in wrapper
    assert "NYTrackerOnSignal(LastApiPayload);" in wrapper
    assert "NYTrackerOnTimer();" in wrapper
    assert "NYTrackerDeinit();" in wrapper


def test_core_preserves_strict_confidence_and_risk_defaults() -> None:
    core = (ROOT / "mt5" / "MetaTraderAI_Core.mqh").read_text(encoding="utf-8")
    assert "input int MinConfidence = 75;" in core
    assert "input double RiskPercent = 0.5;" in core
    assert "const double HARD_MAX_RISK_PERCENT = 0.5;" in core
    assert "input int MaxOpenTrades = 1;" in core
