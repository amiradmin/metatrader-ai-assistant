"""Diagnostic trace for directional M15 candidates that did not become trades.

This module is visualization/debug support only. It does not change the live EA or
its execution rules. It inspects the same historical M15 decisions plus broker M1
data and emits one compact record per directional M15 candidate so MT5 can show
where the EA considered BUY/SELL and why the candidate did not become a trade.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meta_trader_ai.backtest import Candle
from meta_trader_ai.ea_m1_simulator import _build_plan_at_m1_open, _completed_m15_index
from meta_trader_ai.ea_simulator import (
    EAParameters,
    HistoricalDecision,
    SimulationResult,
    _atr_series,
    _ema_series,
)
from meta_trader_ai.models import Action


@dataclass(frozen=True, slots=True)
class CandidateTrace:
    """One directional M15 candidate and its final diagnostic status."""

    time: datetime
    side: Action
    confidence: int
    technical_score: int
    price: float
    status: str
    reason: str


def _candidate_price(candle: Candle, action: Action, point_size: float) -> float:
    if action is Action.BUY:
        return candle.open + candle.spread_points * point_size
    return candle.open


def build_candidate_trace(
    m15: list[Candle],
    m1: list[Candle],
    decisions: list[HistoricalDecision],
    result: SimulationResult,
    *,
    params: EAParameters,
    point_size: float,
    start_index: int,
    end_index: int,
) -> list[CandidateTrace]:
    """Build one chartable diagnostic record per directional M15 decision.

    The live EA checks every 15 seconds while this historical diagnostic has M1
    pseudo-ticks, so WAIT/PULLBACK labels are intentionally diagnostic rather
    than a claim of tick-perfect reconstruction.
    """
    if not m15 or not m1:
        return []

    m15_times = [item.time for item in m15]
    decision_by_index = {item.candle_index: item for item in decisions}
    closes = [item.close for item in m15]
    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    atr = _atr_series(m15, params.atr_period)

    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(max(0, start_index), min(end_index, len(m1) - 1) + 1):
        completed = _completed_m15_index(m15_times, m1[index].time)
        if completed >= 0:
            grouped[completed].append(index)

    opened_by_signal = {trade.signal_time: trade for trade in result.trades}
    traces: list[CandidateTrace] = []
    pending_action: Action | None = None
    pending_started_active_m15_index: int | None = None

    for completed in sorted(grouped):
        decision = decision_by_index.get(completed)
        if decision is None or decision.action not in {Action.BUY, Action.SELL}:
            continue

        indices = grouped[completed]
        first = m1[indices[0]]
        first_price = _candidate_price(first, decision.action, point_size)
        active_m15_index = completed + 1

        opened_trade = opened_by_signal.get(decision.time)
        if opened_trade is not None:
            traces.append(
                CandidateTrace(
                    time=opened_trade.entry_time,
                    side=decision.action,
                    confidence=decision.confidence,
                    technical_score=decision.technical_score,
                    price=opened_trade.entry_price,
                    status="OPENED",
                    reason=opened_trade.entry_type,
                )
            )
            if opened_trade.entry_type.startswith("PULLBACK"):
                pending_action = None
                pending_started_active_m15_index = None
            continue

        if decision.confidence < params.min_confidence:
            traces.append(
                CandidateTrace(
                    time=first.time,
                    side=decision.action,
                    confidence=decision.confidence,
                    technical_score=decision.technical_score,
                    price=first_price,
                    status="REJECTED",
                    reason=f"CONF<{params.min_confidence}",
                )
            )
            continue

        current_atr = atr[completed]
        current_ema9 = ema9[completed]
        current_ema21 = ema21[completed]
        if current_atr is None or current_atr <= 0 or current_ema9 is None or current_ema21 is None:
            traces.append(
                CandidateTrace(
                    time=first.time,
                    side=decision.action,
                    confidence=decision.confidence,
                    technical_score=decision.technical_score,
                    price=first_price,
                    status="REJECTED",
                    reason="INDICATOR_UNAVAILABLE",
                )
            )
            continue

        final_status = "REJECTED"
        final_reason = "FILTERED_OTHER"
        marker_time = first.time
        marker_price = first_price
        had_gate_pass = False

        for index in indices:
            candle = m1[index]
            price = _candidate_price(candle, decision.action, point_size)
            spread_points = candle.spread_points

            if params.max_spread_points > 0 and spread_points > params.max_spread_points:
                final_status = "REJECTED"
                final_reason = "SPREAD_POINTS"
                continue
            spread_to_atr = spread_points * point_size / current_atr
            if params.max_spread_atr_ratio > 0 and spread_to_atr > params.max_spread_atr_ratio:
                final_status = "REJECTED"
                final_reason = "SPREAD_ATR"
                continue

            had_gate_pass = True
            marker_time = candle.time
            marker_price = price

            if not params.use_anti_chase:
                plan = _build_plan_at_m1_open(
                    m15,
                    completed_m15_index=completed,
                    m1_candle=candle,
                    decision=decision,
                    params=params,
                    point_size=point_size,
                    atr=current_atr,
                )
                final_reason = "STOP_PLAN" if plan is None else "FILTERED_OTHER"
                break

            extension_atr = (
                (price - current_ema21) / current_atr
                if decision.action is Action.BUY
                else (current_ema21 - price) / current_atr
            )

            if pending_action is not None and pending_action is not decision.action:
                pending_action = None
                pending_started_active_m15_index = None

            if pending_action is None:
                if extension_atr > params.max_extension_atr:
                    pending_action = decision.action
                    pending_started_active_m15_index = active_m15_index
                    final_status = "WAIT_PULLBACK"
                    final_reason = "ANTI_CHASE"
                    continue

                plan = _build_plan_at_m1_open(
                    m15,
                    completed_m15_index=completed,
                    m1_candle=candle,
                    decision=decision,
                    params=params,
                    point_size=point_size,
                    atr=current_atr,
                )
                final_status = "REJECTED"
                final_reason = "STOP_PLAN" if plan is None else "FILTERED_OTHER"
                break

            assert pending_started_active_m15_index is not None
            bars_waited = active_m15_index - pending_started_active_m15_index
            if bars_waited > params.pullback_max_bars:
                pending_action = None
                pending_started_active_m15_index = None
                final_status = "REJECTED"
                final_reason = "PULLBACK_EXPIRED"
                continue

            if extension_atr > params.max_extension_atr:
                final_status = "WAIT_PULLBACK"
                final_reason = "STILL_EXTENDED"
                continue

            if decision.action is Action.BUY:
                trend_aligned = current_ema9 > current_ema21
                distance_atr = (price - current_ema9) / current_atr
                reclaimed = price >= current_ema9 and price >= current_ema21
            else:
                trend_aligned = current_ema9 < current_ema21
                distance_atr = (current_ema9 - price) / current_atr
                reclaimed = price <= current_ema9 and price <= current_ema21

            in_zone = 0.0 <= distance_atr <= params.pullback_zone_atr
            if not (trend_aligned and reclaimed and in_zone):
                final_status = "WAIT_PULLBACK"
                final_reason = "PULLBACK_NOT_READY"
                continue

            plan = _build_plan_at_m1_open(
                m15,
                completed_m15_index=completed,
                m1_candle=candle,
                decision=decision,
                params=params,
                point_size=point_size,
                atr=current_atr,
            )
            final_status = "REJECTED"
            final_reason = "STOP_PLAN" if plan is None else "FILTERED_OTHER"
            if plan is not None:
                pending_action = None
                pending_started_active_m15_index = None
            break

        if not had_gate_pass and final_reason == "FILTERED_OTHER":
            final_reason = "NO_EXECUTION_MINUTE"

        traces.append(
            CandidateTrace(
                time=marker_time,
                side=decision.action,
                confidence=decision.confidence,
                technical_score=decision.technical_score,
                price=marker_price,
                status=final_status,
                reason=final_reason,
            )
        )

    return traces


def write_candidate_trace(path: Path, traces: list[CandidateTrace]) -> None:
    """Write candidate diagnostics in a compact MT5-friendly CSV format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "time",
                "side",
                "confidence",
                "technical_score",
                "price",
                "status",
                "reason",
            ]
        )
        for item in traces:
            writer.writerow(
                [
                    item.time.isoformat(sep=" "),
                    item.side.value,
                    item.confidence,
                    item.technical_score,
                    f"{item.price:.5f}",
                    item.status,
                    item.reason,
                ]
            )
