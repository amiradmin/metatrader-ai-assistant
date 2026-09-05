"""FastAPI entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException

from meta_trader_ai.bridge import SnapshotError, load_snapshot
from meta_trader_ai.calendar_service import collect_calendar_news_resilient
from meta_trader_ai.config import settings
from meta_trader_ai.course_decision_tree import (
    CourseDecisionConfig,
    apply_course_decision_tree,
)
from meta_trader_ai.economic_calendar import EconomicCalendarError
from meta_trader_ai.market_structure import MarketStructureError, load_structure_context
from meta_trader_ai.models import NewsCoverage, NewsItem, TipRanksContext, TradeHint
from meta_trader_ai.news import NewsCollection, collect_news_report
from meta_trader_ai.risk_controls import apply_pretrade_controls
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


async def _collect_signal_news(symbol: str) -> tuple[list[NewsItem], NewsCoverage, int]:
    """Collect external news under a hard latency budget.

    MT5/Wine can surface a non-standard HTTP 1003 when WebRequest waits too long.
    Signal generation must therefore never wait for slow RSS/calendar providers.
    Timed-out sources are reported as unavailable and the signal engine applies
    its normal confidence penalty instead of losing the whole /hint response.
    """
    rss_task: asyncio.Task[NewsCollection] = asyncio.create_task(
        collect_news_report(settings.rss_urls, settings.news_lookback_hours)
    )
    calendar_task: asyncio.Task[list[NewsItem]] | None = None
    if settings.economic_calendar_enabled:
        calendar_task = asyncio.create_task(
            collect_calendar_news_resilient(
                symbol,
                settings.forex_factory_calendar_url,
                disk_cache_path=settings.economic_calendar_disk_cache_path,
                disk_stale_minutes=settings.economic_calendar_disk_stale_minutes,
                cache_seconds=settings.economic_calendar_cache_seconds,
                stale_fallback_minutes=settings.economic_calendar_stale_fallback_minutes,
                request_timeout_seconds=settings.economic_calendar_request_timeout_seconds,
                failure_cooldown_seconds=settings.economic_calendar_failure_cooldown_seconds,
                max_attempts=settings.economic_calendar_max_attempts,
                high_before_minutes=settings.economic_calendar_high_before_minutes,
                high_after_minutes=settings.economic_calendar_high_after_minutes,
                medium_before_minutes=settings.economic_calendar_medium_before_minutes,
                medium_after_minutes=settings.economic_calendar_medium_after_minutes,
            )
        )

    tasks: set[asyncio.Task[object]] = {rss_task}
    if calendar_task is not None:
        tasks.add(calendar_task)

    timeout_seconds = max(0.5, settings.signal_external_data_timeout_seconds)
    _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        logger.warning(
            "Signal external-data deadline reached after %.2fs; using degraded news coverage.",
            timeout_seconds,
        )

    news: list[NewsItem] = []
    successful_sources = 0
    failed_sources = 0

    if rss_task.cancelled() or not rss_task.done():
        failed_sources += len(settings.rss_urls)
    else:
        try:
            rss_news = rss_task.result()
        except Exception as exc:
            failed_sources += len(settings.rss_urls)
            logger.warning("RSS news collection failed: %s", exc)
        else:
            news.extend(rss_news.items)
            successful_sources += rss_news.total_sources - rss_news.failed_sources
            failed_sources += rss_news.failed_sources

    if calendar_task is not None:
        if calendar_task.cancelled() or not calendar_task.done():
            failed_sources += 1
        else:
            try:
                calendar_news = calendar_task.result()
            except EconomicCalendarError as exc:
                failed_sources += 1
                logger.warning("Economic calendar check failed: %s", exc)
            except Exception as exc:
                failed_sources += 1
                logger.warning("Economic calendar task failed: %s", exc)
            else:
                news.extend(calendar_news)
                successful_sources += 1

    if successful_sources == 0:
        coverage = NewsCoverage.UNAVAILABLE
    elif failed_sources:
        coverage = NewsCoverage.PARTIAL
    else:
        coverage = NewsCoverage.COMPLETE

    return news, coverage, failed_sources


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
    version="0.5.0-course-tree",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "guarded-course-tree",
        "course_decision_tree": (
            "enabled" if settings.course_decision_tree_enabled else "disabled"
        ),
        "economic_calendar": (
            "enabled-degraded"
            if settings.economic_calendar_enabled
            else "disabled"
        ),
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

    market_structure_context = None
    if settings.market_structure_enabled:
        try:
            market_structure_context = load_structure_context(
                settings.mt5_context_path,
                symbol=snapshot.symbol,
                max_age_seconds=settings.max_context_age_seconds,
            )
        except MarketStructureError:
            market_structure_context = None

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

    news, news_coverage, failed_news_sources = await _collect_signal_news(snapshot.symbol)

    trade_hint = build_hint(
        snapshot,
        news,
        settings.max_risk_percent,
        tipranks_context=tipranks_context,
        market_structure_context=market_structure_context,
        news_coverage=news_coverage,
        failed_news_sources=failed_news_sources,
    )
    if settings.course_decision_tree_enabled:
        trade_hint = apply_course_decision_tree(
            snapshot,
            trade_hint,
            CourseDecisionConfig(
                range_lookback=settings.course_range_lookback,
                trend_lookback=settings.course_trend_lookback,
                range_edge_fraction=settings.course_range_edge_fraction,
                touch_tolerance_atr=settings.course_touch_tolerance_atr,
                breakout_step_bars=settings.course_breakout_step_bars,
                breakout_body_multiple=settings.course_breakout_body_multiple,
            ),
        )
    return apply_pretrade_controls(
        snapshot,
        trade_hint,
        max_daily_loss_percent=settings.max_daily_loss_percent,
        max_spread_atr_ratio=settings.max_spread_atr_ratio,
        min_entry_confidence=settings.strict_entry_min_confidence,
    )
