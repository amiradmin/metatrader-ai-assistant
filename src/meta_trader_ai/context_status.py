"""Check the observational multi-source market context without trading."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from meta_trader_ai.config import settings
from meta_trader_ai.market_context import MarketContextCollector
from meta_trader_ai.research_context import ResearchContextCollector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument(
        "--mt5-context",
        type=Path,
        default=settings.mt5_snapshot_path.with_name("mt5_context.json"),
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/market_context_cache.json"),
    )
    parser.add_argument(
        "--research-cache",
        type=Path,
        default=Path("data/research_context_cache.json"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    now = datetime.now(UTC)
    collector = MarketContextCollector(
        mt5_context_path=args.mt5_context,
        cache_path=args.cache,
    )
    research_collector = ResearchContextCollector(cache_path=args.research_cache)
    context = collector.collect(symbol=args.symbol, now=now)
    research = research_collector.collect(now=now)

    print("MARKET CONTEXT STATUS")
    print("=" * 72)
    print(f"MT5 context file: {args.mt5_context}")
    print(f"H1: trend={context.h1_trend} volatility={context.h1_volatility}")
    print(f"H4: trend={context.h4_trend} volatility={context.h4_volatility}")
    print(
        "US 10Y real yield: "
        f"{context.real_yield_10y}  change={context.real_yield_change_bp} bp  "
        f"date={context.real_yield_date or '-'}"
    )

    if research.dxy_value is not None:
        proxy = " PROXY" if research.dxy_is_proxy else ""
        print(
            f"Dollar index{proxy}: {research.dxy_value:.3f}  "
            f"1d={research.dxy_change_1d_pct if research.dxy_change_1d_pct is not None else '-'}%  "
            f"5d={research.dxy_change_5d_pct if research.dxy_change_5d_pct is not None else '-'}%  "
            f"trend={research.dxy_trend_5d}"
        )
        print(
            f"Dollar source: {research.dxy_source}  "
            f"date={research.dxy_observation_date or '-'}"
        )
    else:
        print("Dollar index: unavailable")

    if research.cot_mm_net is not None:
        print(
            "CFTC COMEX Gold Managed Money: "
            f"long={research.cot_mm_long} short={research.cot_mm_short} "
            f"net={research.cot_mm_net:+d} "
            f"weekly_net_change={research.cot_mm_net_change:+d} "
            f"report={research.cot_gold_report_date}"
        )
    else:
        print("CFTC COMEX Gold Managed Money: unavailable")

    if research.ff_next_event_name:
        ff_minutes = (
            f"{research.ff_minutes_to_event:.0f} minutes"
            if research.ff_minutes_to_event is not None
            else "unknown"
        )
        print(
            f"FF next high-impact USD: {research.ff_next_event_name} "
            f"[{research.ff_next_event_impact}] in {ff_minutes}"
        )
        print(
            f"FF event UTC: {research.ff_next_event_utc}  "
            f"forecast={research.ff_forecast or '-'} previous={research.ff_previous or '-'}"
        )
    else:
        print("FF next high-impact USD: unavailable in current weekly feed")

    if context.next_event_name:
        minutes = (
            f"{context.minutes_to_event:.0f} minutes"
            if context.minutes_to_event is not None
            else "unknown"
        )
        print(
            f"Official next event: {context.next_event_name} [{context.next_event_impact}] "
            f"source={context.next_event_source} in {minutes}"
        )
        print(
            f"Official event UTC: {context.next_event_utc} "
            f"timing={context.event_timing_quality}"
        )
    else:
        print("Official next event: unavailable")

    if context.context_errors:
        if research.ff_next_event_name:
            print(f"Official-source warning (FF fallback available): {context.context_errors}")
        else:
            print(f"Official-source warning: {context.context_errors}")
    else:
        print("Official-source warnings: none")

    if research.research_errors:
        print(f"Research-source warnings: {research.research_errors}")
    else:
        print("Research-source warnings: none")
    print("Decision impact: NONE (observer-only context)")


if __name__ == "__main__":
    main()
