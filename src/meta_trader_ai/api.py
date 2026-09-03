"""FastAPI entry point."""

from fastapi import FastAPI, HTTPException

from meta_trader_ai.bridge import SnapshotError, load_snapshot
from meta_trader_ai.config import settings
from meta_trader_ai.models import TipRanksContext, TradeHint
from meta_trader_ai.news import collect_news
from meta_trader_ai.signals import build_hint
from meta_trader_ai.tipranks import TipRanksContextError, load_context, save_context

app = FastAPI(title="MetaTrader AI Assistant", version="0.2.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "read-only"}


@app.put("/context/tipranks", response_model=TipRanksContext)
def put_tipranks_context(context: TipRanksContext) -> TipRanksContext:
    """Store external TipRanks data locally; this never places or modifies orders."""
    save_context(settings.tipranks_context_path, context)
    return context


@app.get("/hint", response_model=TradeHint)
async def hint() -> TradeHint:
    try:
        snapshot = load_snapshot(
            settings.mt5_snapshot_path,
            settings.max_snapshot_age_seconds,
        )
    except SnapshotError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    tipranks_context = None
    if settings.tipranks_context_enabled:
        try:
            tipranks_context = load_context(
                settings.tipranks_context_path,
                snapshot.symbol,
                settings.tipranks_context_max_age_minutes,
            )
        except TipRanksContextError:
            tipranks_context = None

    news = await collect_news(settings.rss_urls, settings.news_lookback_hours)
    return build_hint(
        snapshot,
        news,
        settings.max_risk_percent,
        tipranks_context=tipranks_context,
    )
