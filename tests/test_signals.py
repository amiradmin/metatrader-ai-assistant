from datetime import datetime, timezone

from meta_trader_ai.models import Action, MarketSnapshot, NewsItem
from meta_trader_ai.signals import build_hint


def snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="EURUSD",
        timeframe="M15",
        generated_at=datetime.now(timezone.utc),
        bid=1.1,
        ask=1.1001,
        balance=1000,
        equity=1000,
        closes=[1 + i / 1000 for i in range(20)],
    )


def test_high_impact_news_forces_wait() -> None:
    news = [
        NewsItem(
            source="Federal Reserve",
            title="FOMC interest rate decision",
            url="https://example.test",
            currencies={"USD"},
            impact_score=80,
        )
    ]
    hint = build_hint(snapshot(), news, 0.5)
    assert hint.action is Action.WAIT
