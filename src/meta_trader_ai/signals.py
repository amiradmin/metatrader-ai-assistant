"""Explainable baseline signal engine; never places orders."""

from datetime import datetime, timezone

from meta_trader_ai.models import Action, MarketSnapshot, NewsItem, NewsRisk, TradeHint
from meta_trader_ai.news import risk_for_symbol


def _sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def build_hint(
    snapshot: MarketSnapshot,
    news: list[NewsItem],
    max_risk_percent: float,
) -> TradeHint:
    """Combine a simple trend score with a hard news-risk gate."""
    fast = _sma(snapshot.closes, 10)
    slow = _sma(snapshot.closes, 20)
    spread = snapshot.ask - snapshot.bid
    technical_score = 60 if fast > slow else -60 if fast < slow else 0
    news_risk = risk_for_symbol(snapshot.symbol, news)
    reasons = [
        f"SMA10={fast:.5f}, SMA20={slow:.5f}",
        f"Current spread={spread:.5f}",
        f"News risk={news_risk.value}",
    ]

    if news_risk is NewsRisk.HIGH:
        action = Action.WAIT
        confidence = 80
        reasons.append("High-impact news gate blocked new entries.")
    elif technical_score > 0:
        action = Action.BUY
        confidence = 55 if news_risk is NewsRisk.LOW else 40
    elif technical_score < 0:
        action = Action.SELL
        confidence = 55 if news_risk is NewsRisk.LOW else 40
    else:
        action = Action.WAIT
        confidence = 50

    currencies = {snapshot.symbol[:3].upper(), snapshot.symbol[3:6].upper()}
    relevant = sorted(
        (item for item in news if item.impact_score > 0 and item.currencies & currencies),
        key=lambda item: item.impact_score,
        reverse=True,
    )[:5]

    return TradeHint(
        action=action,
        symbol=snapshot.symbol,
        confidence=confidence,
        technical_score=technical_score,
        news_risk=news_risk,
        reasons=reasons,
        relevant_news=relevant,
        max_risk_percent=max_risk_percent,
        generated_at=datetime.now(timezone.utc),
    )
