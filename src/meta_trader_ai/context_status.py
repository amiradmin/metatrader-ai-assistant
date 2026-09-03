"""Check the observational H1/H4 and macro context layer without trading."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from meta_trader_ai.config import settings
from meta_trader_ai.market_context import MarketContextCollector


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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    collector = MarketContextCollector(
        mt5_context_path=args.mt5_context,
        cache_path=args.cache,
    )
    context = collector.collect(symbol=args.symbol, now=datetime.now(UTC))

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
    if context.next_event_name:
        minutes = (
            f"{context.minutes_to_event:.0f} minutes"
            if context.minutes_to_event is not None
            else "unknown"
        )
        print(
            f"Next event: {context.next_event_name} [{context.next_event_impact}] "
            f"source={context.next_event_source} in {minutes}"
        )
        print(
            f"Event UTC: {context.next_event_utc} "
            f"timing={context.event_timing_quality}"
        )
    else:
        print("Next event: unavailable")

    if context.context_errors:
        print(f"Warnings: {context.context_errors}")
    else:
        print("Warnings: none")
    print("Decision impact: NONE (observer-only context)")


if __name__ == "__main__":
    main()
