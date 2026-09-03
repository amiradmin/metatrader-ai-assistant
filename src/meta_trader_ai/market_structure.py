"""Confirmed-swing market-structure analysis for read-only MT5 context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StructureFeatures:
    """Market-structure features derived only from completed candles."""

    trend: str = "UNAVAILABLE"
    sequence: str = "UNAVAILABLE"
    event: str = "NONE"
    last_swing_high: float | None = None
    previous_swing_high: float | None = None
    last_swing_low: float | None = None
    previous_swing_low: float | None = None


@dataclass(frozen=True, slots=True)
class MultiTimeframeStructure:
    """H1/H4 structure context exported by the read-only MT5 bridge."""

    h1: StructureFeatures
    h4: StructureFeatures


class MarketStructureError(RuntimeError):
    """Raised when higher-timeframe structure context cannot be trusted."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_iso(value: str) -> datetime:
    return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _confirmed_swings(
    values: list[float],
    *,
    high: bool,
    left: int = 2,
    right: int = 2,
) -> list[tuple[int, float]]:
    """Return confirmed fractal swings; right-side bars prevent repainting."""
    if len(values) < left + right + 1:
        return []

    points: list[tuple[int, float]] = []
    for index in range(left, len(values) - right):
        value = values[index]
        left_values = values[index - left : index]
        right_values = values[index + 1 : index + right + 1]
        if high:
            confirmed = all(value > item for item in left_values) and all(
                value >= item for item in right_values
            )
        else:
            confirmed = all(value < item for item in left_values) and all(
                value <= item for item in right_values
            )
        if confirmed:
            points.append((index, value))
    return points


def detect_structure(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    swing_left: int = 2,
    swing_right: int = 2,
) -> StructureFeatures:
    """Classify HH/HL, LH/LL and the latest confirmed BOS/CHOCH event."""
    if (
        len(closes) < 12
        or len(highs) != len(closes)
        or len(lows) != len(closes)
    ):
        return StructureFeatures()

    swing_highs = _confirmed_swings(
        highs,
        high=True,
        left=swing_left,
        right=swing_right,
    )
    swing_lows = _confirmed_swings(
        lows,
        high=False,
        left=swing_left,
        right=swing_right,
    )
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return StructureFeatures(trend="RANGE", sequence="INSUFFICIENT_SWINGS")

    previous_high = swing_highs[-2][1]
    last_high = swing_highs[-1][1]
    previous_low = swing_lows[-2][1]
    last_low = swing_lows[-1][1]

    if last_high > previous_high and last_low > previous_low:
        trend = "BULLISH"
        sequence = "HH_HL"
    elif last_high < previous_high and last_low < previous_low:
        trend = "BEARISH"
        sequence = "LH_LL"
    else:
        trend = "RANGE"
        sequence = "MIXED"

    event = "NONE"
    previous_close = closes[-2]
    latest_close = closes[-1]
    if previous_close <= last_high < latest_close:
        event = "CHOCH_UP" if trend == "BEARISH" else "BOS_UP"
    elif previous_close >= last_low > latest_close:
        event = "CHOCH_DOWN" if trend == "BULLISH" else "BOS_DOWN"

    return StructureFeatures(
        trend=trend,
        sequence=sequence,
        event=event,
        last_swing_high=last_high,
        previous_swing_high=previous_high,
        last_swing_low=last_low,
        previous_swing_low=previous_low,
    )


def _float_list(raw: object) -> list[float]:
    if not isinstance(raw, list):
        return []
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return []


def _timeframe_structure(payload: dict[str, object], key: str) -> StructureFeatures:
    raw = payload.get(key)
    if not isinstance(raw, dict):
        return StructureFeatures()
    highs = _float_list(raw.get("highs"))
    lows = _float_list(raw.get("lows"))
    closes = _float_list(raw.get("closes"))
    return detect_structure(highs, lows, closes)


def load_structure_context(
    path: Path,
    *,
    symbol: str,
    max_age_seconds: int = 90,
    now: datetime | None = None,
) -> MultiTimeframeStructure:
    """Load fresh H1/H4 structure exported by ReadOnlyMarketContextBridge.mq5."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketStructureError(f"MT5 structure context unavailable: {exc}") from exc

    if str(payload.get("symbol", "")) != symbol:
        raise MarketStructureError(
            f"MT5 structure symbol mismatch: {payload.get('symbol')} != {symbol}"
        )

    try:
        generated_at = _parse_iso(str(payload["generated_at"]))
    except (KeyError, ValueError) as exc:
        raise MarketStructureError("MT5 structure generated_at is invalid") from exc

    age = (_aware_utc(now or datetime.now(UTC)) - generated_at).total_seconds()
    if age > max_age_seconds:
        raise MarketStructureError(f"MT5 structure context is stale ({age:.1f}s old)")
    if age < -5:
        raise MarketStructureError(
            f"MT5 structure timestamp is {abs(age):.1f}s in the future"
        )

    h1 = _timeframe_structure(payload, "h1")
    h4 = _timeframe_structure(payload, "h4")
    if h1.trend == "UNAVAILABLE" or h4.trend == "UNAVAILABLE":
        raise MarketStructureError("MT5 H1/H4 structure arrays are incomplete")
    return MultiTimeframeStructure(h1=h1, h4=h4)
