from datetime import UTC, datetime

from meta_trader_ai.models import MarketSnapshot
from meta_trader_ai.shadow_forward import evaluate_snapshot, m15_bucket


def test_m15_bucket_uses_utc_boundary() -> None:
    value = datetime(2026, 9, 3, 10, 29, 59, tzinfo=UTC)
    assert m15_bucket(value) == "2026-09-03T10:15:00+00:00"


def test_flat_snapshot_is_not_shadow_eligible() -> None:
    closes = [100.0] * 100
    snapshot = MarketSnapshot(
        symbol="XAUUSD_o",
        timeframe="M15",
        generated_at=datetime(2026, 9, 3, 10, 30, tzinfo=UTC),
        bid=100.0,
        ask=100.2,
        balance=10_000.0,
        equity=10_000.0,
        positions_total=0,
        opens=closes,
        highs=[100.5] * 100,
        lows=[99.5] * 100,
        closes=closes,
    )
    decision = evaluate_snapshot(snapshot)
    assert not decision.eligible
    assert decision.baseline_action == "WAIT"
