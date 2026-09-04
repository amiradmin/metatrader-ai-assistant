from __future__ import annotations

from meta_trader_ai.desktop_notifier import is_notifiable, notification_from_payload


def payload(*, action: str = "WAIT", risk_guard_status: str = "OK") -> dict[str, object]:
    return {
        "action": action,
        "symbol": "XAUUSD_o",
        "generated_at": "2026-09-04T11:45:01Z",
        "confidence": 75,
        "news_risk": "LOW",
        "news_coverage": "COMPLETE",
        "mtf_status": "CONFIRM",
        "max_risk_percent": 0.5,
        "risk_guard_status": risk_guard_status,
    }


def test_only_guarded_directional_hints_are_notifiable() -> None:
    assert is_notifiable(payload(action="BUY"))
    assert is_notifiable(payload(action="SELL"))
    assert not is_notifiable(payload(action="BUY") | {"confidence": 74})
    assert not is_notifiable(payload())
    assert not is_notifiable(payload(action="BUY", risk_guard_status="DAILY_LOSS_LIMIT"))


def test_notification_contains_review_context() -> None:
    notification = notification_from_payload(payload(action="BUY"))

    assert notification.title == "MetaTrader AI: BUY review — XAUUSD_o"
    assert notification.key == ("2026-09-04T11:45:00+00:00", "XAUUSD_o")
    assert "confidence 75/100" in notification.body
    assert "manual review required; no order was placed" in notification.body
