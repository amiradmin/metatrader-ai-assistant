"""FastAPI entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException

from meta_trader_ai.bridge import SnapshotError, load_snapshot
from meta_trader_ai.config import settings
from meta_trader_ai.models import TipRanksContext, TradeHint
from meta_trader_ai.news import collect_news
from meta_trader_ai.signals import build_hint
from meta_trader_ai.tipranks import TipRanksContextError, load_context, save_context
from meta_trader_ai.tipranks_mcp import TipRanksMcpError, fetch_forex_context

logger = logging.getLogger(__name__)


async def refresh_tipranks_context() -> TipRanksContext:
    """Refresh TipRanks context for the symbol currently exported by MT5."""
    if not settings.tipranks_context_enabled:
        raise TipRanksMcpError("TipRanks context is disabled")
    if not settings.tipranks_mcp_api_key.strip():
        raise TipRanksMcpError("TIPRANKS_MCP_API_KEY is not configured")

    try:
        snapshot = load_snapshot(
            settings.mt5_snapshot_path,
            settings.max_snapshot_age_seconds,
        )
    except SnapshotError as exc:
        raise TipRanksMcpError(f"Cannot refresh without a fresh MT5 snapshot: {exc}") from exc

    context = await fetch_forex_context(
        snapshot.symbol,
        settings.tipranks_mcp_api_key,
        settings.tipranks_mcp_url,
    )
    save_context(settings.tipranks_context_path, context)
    logger.info(
        "TipRanks context refreshed for %s at %s",
        context.symbol,
        context.updated_at.isoformat(),
    )
    return context


async def _tipranks_refresh_loop() -> None:
    """Refresh immediately, then periodically without blocking the signal API."""
    interval_seconds = max(5, settings.tipranks_refresh_minutes) * 60
    while True:
        try:
            await refresh_tipranks_context()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("TipRanks auto-refresh skipped: %s", exc)
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the optional TipRanks background refresh task."""
    del app
    refresh_task: asyncio.Task[None] | None = None
    if (
        settings.tipranks_auto_refresh_enabled
        and settings.tipranks_context_enabled
        and settings.tipranks_mcp_api_key.strip()
    ):
        refresh_task = asyncio.create_task(_tipranks_refresh_loop())

    try:
        yield
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task


app = FastAPI(
    title="MetaTrader AI Assistant",
    version="0.3.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "guarded",
        "tipranks_auto_refresh": (
            "enabled"
            if settings.tipranks_auto_refresh_enabled
            and bool(settings.tipranks_mcp_api_key.strip())
            else "manual"
        ),
    }


@app.put("/context/tipranks", response_model=TipRanksContext)
def put_tipranks_context(context: TipRanksContext) -> TipRanksContext:
    """Store external TipRanks data locally; this never places or modifies orders."""
    save_context(settings.tipranks_context_path, context)
    return context


@app.post("/context/tipranks/refresh", response_model=TipRanksContext)
async def refresh_tipranks_now() -> TipRanksContext:
    """Force one immediate refresh through TipRanks' official MCP endpoint."""
    try:
        return await refresh_tipranks_context()
    except TipRanksMcpError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
