"""FastAPI entry point."""

from fastapi import FastAPI, HTTPException

from meta_trader_ai.bridge import SnapshotError, load_snapshot
from meta_trader_ai.config import settings
from meta_trader_ai.models import TradeHint
from meta_trader_ai.news import collect_news
from meta_trader_ai.signals import build_hint

app = FastAPI(title="MetaTrader AI Assistant", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "read-only"}


@app.get("/hint", response_model=TradeHint)
async def hint() -> TradeHint:
    try:
        snapshot = load_snapshot(
            settings.mt5_snapshot_path,
            settings.max_snapshot_age_seconds,
        )
    except SnapshotError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    news = await collect_news(settings.rss_urls)
    return build_hint(snapshot, news, settings.max_risk_percent)
