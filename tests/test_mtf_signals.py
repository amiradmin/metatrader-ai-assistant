from datetime import UTC, datetime

from meta_trader_ai.market_structure import MultiTimeframeStructure, StructureFeatures
from meta_trader_ai.models import Action, MarketSnapshot
from meta_trader_ai.signals import build_hint


def _snapshot() -> MarketSnapshot:
    closes = [1 + index / 1000 for index in range(20)]
    return MarketSnapshot(
        symbol="EURUSD",
        timeframe="PERIOD_M15",
        generated_at=datetime.now(UTC),
        bid=closes[-1],
        ask=closes[-1] + 0.0001,
        balance=1000,
        equity=1000,
        closes=closes,
    )


def test_mtf_structure_is_observational_and_does_not_change_confidence() -> None:
    context = MultiTimeframeStructure(
        h1=StructureFeatures(trend="BULLISH", sequence="HH_HL", event="BOS_UP"),
        h4=StructureFeatures(trend="BULLISH", sequence="HH_HL", event="NONE"),
    )

    baseline = build_hint(_snapshot(), [], 0.5)
    contextual = build_hint(
        _snapshot(),
        [],
        0.5,
        market_structure_context=context,
    )

    assert baseline.action is Action.BUY
    assert contextual.action is Action.BUY
    assert contextual.confidence == baseline.confidence
    assert contextual.mtf_status == "CONFIRM"
    assert contextual.h1_trend == "BULLISH"
    assert contextual.h1_structure == "HH_HL"
    assert contextual.h1_structure_event == "BOS_UP"
    assert contextual.h4_trend == "BULLISH"
    assert any("Observer only" in reason for reason in contextual.reasons)
