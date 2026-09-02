"""Shared domain models."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


class NewsRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class MarketSnapshot(BaseModel):
    symbol: str
    timeframe: str
    generated_at: datetime
    bid: float
    ask: float
    balance: float
    equity: float
    positions_total: int = 0
    closes: list[float] = Field(min_length=20)


class NewsItem(BaseModel):
    source: str
    title: str
    url: str
    published_at: datetime | None = None
    currencies: set[str] = Field(default_factory=set)
    impact_score: int = Field(default=0, ge=0, le=100)


class TradeHint(BaseModel):
    action: Action
    symbol: str
    confidence: int = Field(ge=0, le=100)
    technical_score: int = Field(ge=-100, le=100)
    news_risk: NewsRisk
    reasons: list[str]
    relevant_news: list[NewsItem]
    max_risk_percent: float
    generated_at: datetime
