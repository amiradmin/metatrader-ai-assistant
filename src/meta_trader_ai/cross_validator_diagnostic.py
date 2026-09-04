"""Compare the repo signal/execution gates with the LONA validator candle by candle.

Research-only diagnostic. It never places orders and never changes live EA settings.
The repo side uses ``generate_decisions`` exactly.  The LONA side intentionally
mirrors the first Backtrader validator: recursive EMA/Wilder ATR, simple RSI,
price-only confidence, and the same C75 threshold.  The output explains whether
zero trades come from signal disagreement or from the anti-chase entry gate.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from meta_trader_ai.backtest import Candle, load_candles
from meta_trader_ai.ea_simulator import _atr_series, _ema_series, generate_decisions
from meta_trader_ai.models import Action


@dataclass(frozen=True, slots=True)
class LonaLikeDecision:
    candle_index: int
    action: Action
    confidence: int
    technical_score: int
    ema9: float
    ema21: float
    atr14: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _simple_rsi(closes: list[float], index: int, period: int = 14) -> float:
    if index < period:
        return 50.0
    gains = 0.0
    losses = 0.0
    for offset in range(period):
        current = closes[index - offset]
        previous = closes[index - offset - 1]
        delta = current - previous
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    gains /= period
    losses /= period
    if gains == 0.0 and losses == 0.0:
        return 50.0
    if losses == 0.0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def _lona_like_decisions(candles: list[Candle]) -> list[LonaLikeDecision]:
    closes = [item.close for item in candles]
    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    atr14 = _atr_series(candles, 14)
    result: list[LonaLikeDecision] = []

    for index in range(30, len(candles) - 1):
        fast = ema9[index]
        slow = ema21[index]
        atr = atr14[index]
        if fast is None or slow is None or atr is None or atr <= 0.0:
            continue

        rsi = _simple_rsi(closes, index)
        trend_component = _clamp(((fast - slow) / atr) * 18.0, -45.0, 45.0)
        rsi_component = _clamp((rsi - 50.0) * 0.4, -20.0, 20.0)
        momentum_4 = (closes[index] - closes[index - 4]) / atr
        momentum_component = _clamp(momentum_4 * 4.0, -15.0, 15.0)
        score = int(round(_clamp(
            trend_component + rsi_component + momentum_component,
            -100.0,
            100.0,
        )))

        action = Action.WAIT
        if score >= 30:
            action = Action.BUY
        elif score <= -30:
            action = Action.SELL

        confidence = 48.0 + min(34.0, abs(score) * 0.4)
        aligned = (
            action is Action.BUY and fast > slow and rsi >= 52.0
        ) or (
            action is Action.SELL and fast < slow and rsi <= 48.0
        )
        if aligned:
            confidence += 6.0
        # The LONA technical validator deliberately assumes healthy spread/news.
        confidence += 2.0
        confidence_int = int(round(_clamp(confidence, 0.0, 100.0)))

        result.append(LonaLikeDecision(
            candle_index=index,
            action=action,
            confidence=confidence_int,
            technical_score=score,
            ema9=fast,
            ema21=slow,
            atr14=atr,
        ))
    return result


def _repo_anti_chase_diagnostic(
    candles: list[Candle],
    *,
    min_confidence: int,
    point_size: float,
    max_extension_atr: float,
    pullback_zone_atr: float,
    pullback_max_bars: int,
) -> tuple[Counter[str], list[int], float | None]:
    decisions = generate_decisions(
        candles,
        symbol="XAUUSD_o",
        point_size=point_size,
        lookback_bars=100,
    )
    decision_by_index = {item.candle_index: item for item in decisions}
    closes = [item.close for item in candles]
    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    atr = _atr_series(candles, 14)

    pending_action: Action | None = None
    pending_start_index: int | None = None
    blocked: Counter[str] = Counter()
    passed: list[int] = []
    minimum_initial_extension: float | None = None

    for entry_index in range(100, len(candles)):
        decision = decision_by_index.get(entry_index - 1)
        if decision is None:
            continue
        if decision.action not in {Action.BUY, Action.SELL}:
            blocked["wait"] += 1
            continue
        if decision.confidence < min_confidence:
            blocked["confidence"] += 1
            continue

        candle = candles[entry_index]
        if candle.spread_points > 50:
            blocked["spread_points"] += 1
            continue

        completed = entry_index - 1
        current_atr = atr[completed]
        fast = ema9[completed]
        slow = ema21[completed]
        if current_atr is None or current_atr <= 0.0 or fast is None or slow is None:
            blocked["indicator_unavailable"] += 1
            continue

        if candle.spread_points * point_size / current_atr > 0.25:
            blocked["spread_atr"] += 1
            continue

        entry = (
            candle.open + candle.spread_points * point_size
            if decision.action is Action.BUY
            else candle.open
        )
        extension = (
            (entry - slow) / current_atr
            if decision.action is Action.BUY
            else (slow - entry) / current_atr
        )

        if pending_action is not None and pending_action is not decision.action:
            pending_action = None
            pending_start_index = None

        if pending_action is None:
            if minimum_initial_extension is None or extension < minimum_initial_extension:
                minimum_initial_extension = extension
            if extension > max_extension_atr:
                pending_action = decision.action
                pending_start_index = entry_index
                blocked["anti_chase_started"] += 1
                continue
            passed.append(entry_index)
            continue

        assert pending_start_index is not None
        if entry_index - pending_start_index > pullback_max_bars:
            pending_action = None
            pending_start_index = None
            blocked["pullback_expired"] += 1
            continue
        if extension > max_extension_atr:
            blocked["pullback_still_extended"] += 1
            continue

        if decision.action is Action.BUY:
            trend_aligned = fast > slow
            distance_atr = (entry - fast) / current_atr
            reclaimed = entry >= fast and entry >= slow
        else:
            trend_aligned = fast < slow
            distance_atr = (fast - entry) / current_atr
            reclaimed = entry <= fast and entry <= slow
        in_zone = 0.0 <= distance_atr <= pullback_zone_atr
        if not (trend_aligned and reclaimed and in_zone):
            blocked["pullback_not_ready"] += 1
            continue

        passed.append(entry_index)
        pending_action = None
        pending_start_index = None

    return blocked, passed, minimum_initial_extension


def _write_comparison(
    path: Path,
    candles: list[Candle],
    repo_strict: dict[int, tuple[Action, int, int]],
    lona_strict: dict[int, tuple[Action, int, int]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    indices = sorted(set(repo_strict) | set(lona_strict))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "time", "repo_side", "repo_confidence", "repo_score",
            "lona_side", "lona_confidence", "lona_score", "status",
        ])
        for index in indices:
            repo = repo_strict.get(index)
            lona = lona_strict.get(index)
            if repo and lona:
                status = "MATCH" if repo[0] is lona[0] else "DIRECTION_MISMATCH"
            elif repo:
                status = "REPO_ONLY"
            else:
                status = "LONA_ONLY"
            writer.writerow([
                candles[index].time.isoformat(sep=" "),
                repo[0].value if repo else "",
                repo[1] if repo else "",
                repo[2] if repo else "",
                lona[0].value if lona else "",
                lona[1] if lona else "",
                lona[2] if lona else "",
                status,
            ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path, help="MT5 M15 CSV export")
    parser.add_argument("--confidence", type=int, default=75)
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--max-extension-atr", type=float, default=1.5)
    parser.add_argument("--pullback-zone-atr", type=float, default=0.35)
    parser.add_argument("--pullback-max-bars", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path("data/lona_candle_comparison.csv"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candles = load_candles(args.history)
    repo_decisions = generate_decisions(
        candles,
        symbol="XAUUSD_o",
        point_size=args.point_size,
        lookback_bars=100,
    )
    lona_decisions = _lona_like_decisions(candles)

    repo_strict = {
        item.candle_index: (item.action, item.confidence, item.technical_score)
        for item in repo_decisions
        if item.action in {Action.BUY, Action.SELL}
        and item.confidence >= args.confidence
    }
    lona_strict = {
        item.candle_index: (item.action, item.confidence, item.technical_score)
        for item in lona_decisions
        if item.action in {Action.BUY, Action.SELL}
        and item.confidence >= args.confidence
    }

    shared = set(repo_strict) & set(lona_strict)
    same_direction = sum(repo_strict[index][0] is lona_strict[index][0] for index in shared)
    opposite = len(shared) - same_direction
    overlap_pct = len(shared) / len(repo_strict) * 100.0 if repo_strict else 0.0

    blocked, passed, min_extension = _repo_anti_chase_diagnostic(
        candles,
        min_confidence=args.confidence,
        point_size=args.point_size,
        max_extension_atr=args.max_extension_atr,
        pullback_zone_atr=args.pullback_zone_atr,
        pullback_max_bars=args.pullback_max_bars,
    )
    _write_comparison(args.output, candles, repo_strict, lona_strict)

    print("META TRADER AI - LONA CANDLE CROSS-VALIDATION")
    print("=" * 76)
    print(f"Bars: {len(candles)} | {candles[0].time} -> {candles[-1].time}")
    print(f"Repo C{args.confidence} candidates: {len(repo_strict)}")
    print(f"LONA-like C{args.confidence} candidates: {len(lona_strict)}")
    print(f"Shared candles: {len(shared)} ({overlap_pct:.1f}% of repo candidates)")
    print(f"Shared direction agreement: {same_direction}/{len(shared)} | opposite={opposite}")
    print()
    print("Repo strict anti-chase gate")
    print(f"  passed entry timing: {len(passed)}")
    if min_extension is not None:
        print(f"  minimum initial extension: {min_extension:.3f} ATR")
    for key in (
        "confidence", "spread_points", "spread_atr", "anti_chase_started",
        "pullback_still_extended", "pullback_not_ready", "pullback_expired",
    ):
        print(f"  {key}: {blocked.get(key, 0)}")
    print()
    print(f"Candle comparison CSV: {args.output}")
    print("This diagnostic validates technical/gate agreement only; LONA does not reconstruct historical news/TipRanks or MT5 tick execution.")


if __name__ == "__main__":
    main()
