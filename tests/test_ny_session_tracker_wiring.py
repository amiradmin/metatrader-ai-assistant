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


def test_unified_ea_exposes_three_demo_risk_profiles() -> None:
    wrapper = (ROOT / "mt5" / "MetaTraderAI.mq5").read_text(encoding="utf-8")

    assert "MT_AI_LOW" in wrapper
    assert "MT_AI_MEDIUM" in wrapper
    assert "MT_AI_HIGH" in wrapper
    assert "input ENUM_MT_AI_RISK_MODE RiskMode = MT_AI_LOW;" in wrapper

    # LOW / MEDIUM / HIGH execution intensity.
    assert "if(RiskMode == MT_AI_HIGH) return 50;" in wrapper
    assert "if(RiskMode == MT_AI_MEDIUM) return 78;" in wrapper
    assert "return 82;" in wrapper
    assert "if(RiskMode == MT_AI_HIGH) return 10;" in wrapper
    assert "if(RiskMode == MT_AI_MEDIUM) return 2;" in wrapper
    assert "return 1;" in wrapper
    assert "if(RiskMode == MT_AI_HIGH) return 0.50;" in wrapper
    assert "if(RiskMode == MT_AI_HIGH) return 15.0;" in wrapper


def test_high_profile_is_immediate_demo_stress_mode() -> None:
    wrapper = (ROOT / "mt5" / "MetaTraderAI.mq5").read_text(encoding="utf-8")

    # HIGH may derive direction from technical_score even when the strict API
    # action is WAIT, and deliberately bypasses the normal entry-timing gates.
    assert "string HighDemoDirection(const string json)" in wrapper
    assert 'JsonValue(json, "technical_score")' in wrapper
    assert 'string action = high_demo ? HighDemoDirection(json) : JsonValue(json, "action");' in wrapper
    assert 'if(!high_demo && mtf_status != "CONFIRM") return;' in wrapper
    assert "if(!high_demo)" in wrapper
    assert "EntryTimingAllows(action, current_bar, pullback_reentry)" in wrapper
    assert "EntryCandleConfirmed(action)" in wrapper
    assert "for(int i = 0; i < positions_to_open; i++)" in wrapper

    # Even the wild profile stays demo-only and preserves the basic circuit
    # breakers for account risk, high-impact news and unacceptable spread.
    assert "if(!IsDemoAccount()) return;" in wrapper
    assert 'if(news_risk == "HIGH") return;' in wrapper
    assert 'if(risk_guard != "OK") return;' in wrapper
    assert "RiskModeMaxSpreadPoints()" in wrapper


def test_core_preserves_strict_confidence_and_risk_defaults() -> None:
    core = (ROOT / "mt5" / "MetaTraderAI_Core.mqh").read_text(encoding="utf-8")
    assert "input int MinConfidence = 75;" in core
    assert "input double RiskPercent = 0.5;" in core
    assert "const double HARD_MAX_RISK_PERCENT = 0.5;" in core
    assert "input int MaxOpenTrades = 1;" in core
