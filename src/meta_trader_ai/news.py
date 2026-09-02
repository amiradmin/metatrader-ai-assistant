"""RSS news ingestion and conservative market-impact scoring."""

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import httpx

from meta_trader_ai.models import NewsItem, NewsRisk

HIGH_IMPACT_TERMS = {
    "interest rate": 35,
    "rate decision": 40,
    "inflation": 30,
    "cpi": 35,
    "employment": 25,
    "nonfarm": 40,
    "payroll": 40,
    "gdp": 25,
    "fomc": 40,
    "central bank": 30,
    "sanction": 30,
    "war": 35,
    "oil": 20,
}

CURRENCY_TERMS = {
    "USD": ("federal reserve", "fomc", "united states", "u.s.", "dollar", "bls", "eia"),
    "EUR": ("ecb", "eurozone", "european central bank", "euro"),
    "GBP": ("bank of england", "united kingdom", "britain", "sterling"),
    "JPY": ("bank of japan", "japan", "yen"),
    "CHF": ("swiss national bank", "switzerland", "franc"),
}


def _score(title: str) -> tuple[int, set[str]]:
    text = title.casefold()
    impact = min(100, sum(weight for term, weight in HIGH_IMPACT_TERMS.items() if term in text))
    currencies = {
        currency
        for currency, terms in CURRENCY_TERMS.items()
        if any(term in text for term in terms)
    }
    return impact, currencies


async def fetch_feed(client: httpx.AsyncClient, url: str) -> list[NewsItem]:
    """Fetch one RSS/Atom feed."""
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    source = parsed.feed.get("title") or urlparse(url).netloc
    items: list[NewsItem] = []
    for entry in parsed.entries[:30]:
        title = str(entry.get("title", "")).strip()
        if not title:
            continue
        impact, currencies = _score(title)
        published = entry.get("published_parsed")
        published_at = (
            datetime(*published[:6], tzinfo=timezone.utc) if published else None
        )
        items.append(
            NewsItem(
                source=source,
                title=title,
                url=str(entry.get("link", url)),
                published_at=published_at,
                currencies=currencies,
                impact_score=impact,
            )
        )
    return items


def recent_news(
    items: list[NewsItem],
    lookback_hours: int,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Keep only timestamped items inside the configured decision window."""
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(hours=lookback_hours)
    future_tolerance = current + timedelta(minutes=5)
    return [
        item
        for item in items
        if item.published_at is not None
        and cutoff <= item.published_at <= future_tolerance
    ]


async def collect_news(
    urls: tuple[str, ...],
    lookback_hours: int,
) -> list[NewsItem]:
    """Collect feeds concurrently and discard stale or undated items."""
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "MT5-AI-Assistant/0.1"}) as client:
        results = await asyncio.gather(
            *(fetch_feed(client, url) for url in urls), return_exceptions=True
        )
    collected = [item for result in results if isinstance(result, list) for item in result]
    return recent_news(collected, lookback_hours)


def risk_for_symbol(symbol: str, items: list[NewsItem]) -> NewsRisk:
    """Calculate a conservative news-risk gate for a currency pair."""
    currencies = {symbol[:3].upper(), symbol[3:6].upper()}
    peak = max(
        (item.impact_score for item in items if item.currencies & currencies),
        default=0,
    )
    if peak >= 40:
        return NewsRisk.HIGH
    if peak >= 20:
        return NewsRisk.MEDIUM
    return NewsRisk.LOW
