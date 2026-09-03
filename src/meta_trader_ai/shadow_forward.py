"""Forward-test the frozen momentum candidate without placing broker orders.

The experiment is intentionally isolated from /hint and the MT5 execution EAs.
It reads the existing read-only MT5 snapshot, evaluates the frozen historical
candidate once per new M15 bucket, and records hypothetical BUY trades.

Frozen candidate:
- baseline technical-only hint must be BUY
- trend regime must be TRENDING_UP
- volatility regime must be LOW_VOLATILITY
- 4-bar momentum must be >= 1.50 and < 2.00 ATR
- one shadow position at a time
- default SL=300 points, TP=600 points, point size=0.01

H1/H4, real-yield, calendar, CFTC and dollar-index context is logged to a
separate CSV. Those context fields are observational only and cannot change
eligibility.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from meta_trader_ai.bridge import SnapshotError, load_snapshot
from meta_trader_ai.config import settings
from meta_trader_ai.market_context import MarketContextCollector, MarketContextFeatures
from meta_trader_ai.models import Action, MarketSnapshot
from meta_trader_ai.regime import TrendRegime, VolatilityRegime, classify_regime
from meta_trader_ai.research_context import ResearchContextCollector, ResearchContextFeatures
from meta_trader_ai.signals import _atr, build_hint


MOMENTUM_LOWER = 1.50
MOMENTUM_UPPER = 2.00


@dataclass(frozen=True, slots=True)
class MiniCandle:
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    bucket: str
    symbol: str
    baseline_action: str
    confidence: int
    technical_score: int
    trend_regime: str
    volatility_regime: str
    momentum_4_atr: float
    eligible: bool
    bid: float
    ask: float


@dataclass(slots=True)
class ActiveTrade:
    trade_id: str
    signal_bucket: str
    opened_at_utc: str
    symbol: str
    entry_price: float
    stop_price: float
    target_price: float
    confidence: int
    technical_score: int
    momentum_4_atr: float


def m15_bucket(value: datetime) -> str:
    """Return an offset-aware UTC M15 bucket string."""
    parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    parsed = parsed.replace(
        minute=(parsed.minute // 15) * 15,
        second=0,
        microsecond=0,
    )
    return parsed.isoformat()


def evaluate_snapshot(snapshot: MarketSnapshot) -> ShadowDecision:
    """Evaluate the fixed backtest candidate on one fresh snapshot."""
    if len(snapshot.highs) != len(snapshot.closes) or len(snapshot.lows) != len(snapshot.closes):
        raise ValueError("Shadow forward test requires complete OHLC snapshot arrays")

    candles = [
        MiniCandle(high=high, low=low, close=close)
        for high, low, close in zip(snapshot.highs, snapshot.lows, snapshot.closes)
    ]
    regime = classify_regime(candles)
    atr14 = _atr(snapshot, 14)
    momentum = (snapshot.closes[-1] - snapshot.closes[-5]) / atr14

    # Reproduce the historical technical-only population: no historical news or
    # TipRanks was available in the baseline discovery tests.
    hint = build_hint(
        snapshot,
        news=[],
        max_risk_percent=settings.max_risk_percent,
        tipranks_context=None,
    )

    eligible = (
        hint.action is Action.BUY
        and regime.trend is TrendRegime.TRENDING_UP
        and regime.volatility is VolatilityRegime.LOW_VOLATILITY
        and MOMENTUM_LOWER <= momentum < MOMENTUM_UPPER
    )
    return ShadowDecision(
        bucket=m15_bucket(snapshot.generated_at),
        symbol=snapshot.symbol,
        baseline_action=hint.action.value,
        confidence=hint.confidence,
        technical_score=hint.technical_score,
        trend_regime=regime.trend.value,
        volatility_regime=regime.volatility.value,
        momentum_4_atr=momentum,
        eligible=eligible,
        bid=snapshot.bid,
        ask=snapshot.ask,
    )


def _ensure_csv_schema(path: Path, fields: list[str]) -> None:
    """Add new observer columns without discarding already-collected rows."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        existing_fields = reader.fieldnames or []
        if existing_fields == fields:
            return
        rows = list(reader)

    temporary = path.with_suffix(path.suffix + ".schema_tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    temporary.replace(path)


def _append_csv(path: Path, fields: list[str], row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_csv_schema(path, fields)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()


def _load_state(path: Path) -> tuple[str | None, ActiveTrade | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        last_bucket = payload.get("last_bucket")
        active_raw = payload.get("active")
        active = ActiveTrade(**active_raw) if active_raw else None
        return last_bucket, active
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, None


def _save_state(path: Path, last_bucket: str | None, active: ActiveTrade | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_bucket": last_bucket,
        "active": asdict(active) if active is not None else None,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _record_observation(path: Path, decision: ShadowDecision) -> None:
    row = asdict(decision)
    row["observed_at_utc"] = datetime.now(UTC).isoformat()
    fields = [
        "observed_at_utc",
        "bucket",
        "symbol",
        "baseline_action",
        "confidence",
        "technical_score",
        "trend_regime",
        "volatility_regime",
        "momentum_4_atr",
        "eligible",
        "bid",
        "ask",
    ]
    _append_csv(path, fields, row)


def _record_market_context(
    path: Path,
    decision: ShadowDecision,
    context: MarketContextFeatures,
    research: ResearchContextFeatures,
) -> None:
    """Persist observer context separately so the frozen strategy stays clean."""
    row = asdict(context)
    row.update(asdict(research))
    row.update(
        {
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "bucket": decision.bucket,
            "symbol": decision.symbol,
            "shadow_eligible": decision.eligible,
            "m15_momentum_4_atr": f"{decision.momentum_4_atr:.6f}",
        }
    )
    fields = [
        "observed_at_utc",
        "bucket",
        "symbol",
        "shadow_eligible",
        "m15_momentum_4_atr",
        "h1_trend",
        "h1_volatility",
        "h1_efficiency_ratio",
        "h1_net_move_atr",
        "h1_volatility_ratio",
        "h4_trend",
        "h4_volatility",
        "h4_efficiency_ratio",
        "h4_net_move_atr",
        "h4_volatility_ratio",
        "real_yield_10y",
        "real_yield_change_bp",
        "real_yield_date",
        "next_event_name",
        "next_event_source",
        "next_event_impact",
        "next_event_utc",
        "minutes_to_event",
        "event_timing_quality",
        "ff_next_event_name",
        "ff_next_event_impact",
        "ff_next_event_utc",
        "ff_minutes_to_event",
        "ff_forecast",
        "ff_previous",
        "cot_gold_report_date",
        "cot_mm_long",
        "cot_mm_short",
        "cot_mm_net",
        "cot_mm_net_change",
        "dxy_value",
        "dxy_change_1d_pct",
        "dxy_change_5d_pct",
        "dxy_trend_5d",
        "dxy_observation_date",
        "dxy_source",
        "dxy_is_proxy",
        "context_errors",
        "research_errors",
    ]
    _append_csv(path, fields, row)


def _record_trade_event(
    path: Path,
    *,
    event: str,
    trade: ActiveTrade,
    exit_price: float | None = None,
    outcome: str = "",
    pnl_r: float | None = None,
) -> None:
    fields = [
        "event",
        "event_time_utc",
        "trade_id",
        "signal_bucket",
        "symbol",
        "entry_price",
        "stop_price",
        "target_price",
        "exit_price",
        "outcome",
        "pnl_r",
        "confidence",
        "technical_score",
        "momentum_4_atr",
    ]
    _append_csv(
        path,
        fields,
        {
            "event": event,
            "event_time_utc": datetime.now(UTC).isoformat(),
            "trade_id": trade.trade_id,
            "signal_bucket": trade.signal_bucket,
            "symbol": trade.symbol,
            "entry_price": f"{trade.entry_price:.5f}",
            "stop_price": f"{trade.stop_price:.5f}",
            "target_price": f"{trade.target_price:.5f}",
            "exit_price": "" if exit_price is None else f"{exit_price:.5f}",
            "outcome": outcome,
            "pnl_r": "" if pnl_r is None else f"{pnl_r:.5f}",
            "confidence": trade.confidence,
            "technical_score": trade.technical_score,
            "momentum_4_atr": f"{trade.momentum_4_atr:.6f}",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument("--observations", type=Path, default=Path("data/shadow_observations.csv"))
    parser.add_argument(
        "--context-observations",
        type=Path,
        default=Path("data/shadow_context.csv"),
    )
    parser.add_argument(
        "--mt5-context",
        type=Path,
        default=settings.mt5_snapshot_path.with_name("mt5_context.json"),
    )
    parser.add_argument(
        "--context-cache",
        type=Path,
        default=Path("data/market_context_cache.json"),
    )
    parser.add_argument(
        "--research-cache",
        type=Path,
        default=Path("data/research_context_cache.json"),
    )
    parser.add_argument("--trades", type=Path, default=Path("data/shadow_trades.csv"))
    parser.add_argument("--state", type=Path, default=Path("data/shadow_state.json"))
    args = parser.parse_args()

    stop_distance = args.sl_points * args.point_size
    target_distance = args.tp_points * args.point_size
    if stop_distance <= 0 or target_distance <= 0:
        raise SystemExit("SL/TP and point size must be positive")

    context_collector = MarketContextCollector(
        mt5_context_path=args.mt5_context,
        cache_path=args.context_cache,
    )
    research_collector = ResearchContextCollector(cache_path=args.research_cache)
    last_bucket, active = _load_state(args.state)
    first_fresh_snapshot = last_bucket is None
    print("Frozen shadow candidate: BUY + UP trend + LOW volatility + momentum 1.50-2.00 ATR")
    print("No broker orders are sent. /hint and execution EAs are unchanged.")
    print(
        "Observer context: H1/H4 + real yield + official calendar + "
        "FF high-impact USD + CFTC Gold + DXY (no decision impact)."
    )

    while True:
        try:
            snapshot = load_snapshot(
                settings.mt5_snapshot_path,
                settings.max_snapshot_age_seconds,
            )

            if active is not None:
                if snapshot.bid <= active.stop_price:
                    exit_price = active.stop_price
                    pnl_r = (exit_price - active.entry_price) / stop_distance
                    _record_trade_event(
                        args.trades,
                        event="CLOSE",
                        trade=active,
                        exit_price=exit_price,
                        outcome="STOP",
                        pnl_r=pnl_r,
                    )
                    print(f"shadow STOP {active.trade_id} pnl={pnl_r:+.2f}R")
                    active = None
                elif snapshot.bid >= active.target_price:
                    exit_price = active.target_price
                    pnl_r = (exit_price - active.entry_price) / stop_distance
                    _record_trade_event(
                        args.trades,
                        event="CLOSE",
                        trade=active,
                        exit_price=exit_price,
                        outcome="TARGET",
                        pnl_r=pnl_r,
                    )
                    print(f"shadow TARGET {active.trade_id} pnl={pnl_r:+.2f}R")
                    active = None

            decision = evaluate_snapshot(snapshot)
            if decision.bucket != last_bucket:
                # On first startup, warm up the current candle bucket instead of
                # pretending we entered at its open. From the next M15 boundary
                # onward the first fresh snapshot is a realistic forward entry.
                if first_fresh_snapshot:
                    first_fresh_snapshot = False
                    last_bucket = decision.bucket
                    print(f"warm-up bucket {decision.bucket}; waiting for next M15 boundary")
                else:
                    _record_observation(args.observations, decision)
                    context = context_collector.collect(
                        symbol=decision.symbol,
                        now=snapshot.generated_at,
                    )
                    research = research_collector.collect(now=snapshot.generated_at)
                    _record_market_context(
                        args.context_observations,
                        decision,
                        context,
                        research,
                    )
                    last_bucket = decision.bucket

                    event_text = "none"
                    if research.ff_next_event_name:
                        event_text = (
                            f"{research.ff_next_event_name} in {research.ff_minutes_to_event:.0f}m"
                            if research.ff_minutes_to_event is not None
                            else research.ff_next_event_name
                        )
                    elif context.next_event_name:
                        event_text = (
                            f"{context.next_event_name} in {context.minutes_to_event:.0f}m"
                            if context.minutes_to_event is not None
                            else context.next_event_name
                        )

                    print(
                        "context "
                        f"H1={context.h1_trend} H4={context.h4_trend} "
                        f"realYield={context.real_yield_10y} "
                        f"DXY={research.dxy_value}({research.dxy_trend_5d}) "
                        f"COTnet={research.cot_mm_net} next={event_text}"
                    )
                    if context.context_errors:
                        print(f"official-context warning: {context.context_errors}")
                    if research.research_errors:
                        print(f"research-context warning: {research.research_errors}")

                    if decision.eligible and active is None:
                        active = ActiveTrade(
                            trade_id=uuid4().hex[:12],
                            signal_bucket=decision.bucket,
                            opened_at_utc=datetime.now(UTC).isoformat(),
                            symbol=decision.symbol,
                            entry_price=decision.ask,
                            stop_price=decision.ask - stop_distance,
                            target_price=decision.ask + target_distance,
                            confidence=decision.confidence,
                            technical_score=decision.technical_score,
                            momentum_4_atr=decision.momentum_4_atr,
                        )
                        _record_trade_event(args.trades, event="OPEN", trade=active)
                        print(
                            f"shadow BUY {active.trade_id} entry={active.entry_price:.2f} "
                            f"momentum={active.momentum_4_atr:.2f} ATR"
                        )
                    else:
                        print(
                            f"{decision.bucket} eligible={decision.eligible} "
                            f"action={decision.baseline_action} momentum={decision.momentum_4_atr:.2f}"
                        )

            _save_state(args.state, last_bucket, active)
        except (SnapshotError, ValueError) as exc:
            print(f"shadow waiting: {exc}")

        time.sleep(max(args.interval, 1.0))


if __name__ == "__main__":
    main()
