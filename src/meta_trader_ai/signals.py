"""Explainable M15-first signal engine; never places orders."""

from datetime import datetime, timezone

from meta_trader_ai.models import (
    Action,
    MarketSnapshot,
    NewsItem,
    NewsRisk,
    TipRanksContext,
    TradeHint,
)
from meta_trader_ai.news import risk_for_symbol


MIN_ACTION_CONFIDENCE = 70
M15_TIMEFRAMES = {"M15", "PERIOD_M15"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ema(values: list[float], period: int) -> float:
    """Return an EMA using all available chronological values."""
    alpha = 2.0 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    """Return a simple RSI over the latest completed deltas."""
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    sample = deltas[-period:]
    gains = sum(max(delta, 0.0) for delta in sample) / period
    losses = sum(max(-delta, 0.0) for delta in sample) / period
    if gains == 0.0 and losses == 0.0:
        return 50.0
    if losses == 0.0:
        return 100.0
    relative_strength = gains / losses
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _atr(snapshot: MarketSnapshot, period: int = 14) -> float:
    """Return ATR from OHLC when available, otherwise a close-to-close proxy."""
    closes = snapshot.closes
    has_ohlc = (
        len(snapshot.highs) == len(closes)
        and len(snapshot.lows) == len(closes)
        and len(closes) >= period + 1
    )
    if has_ohlc:
        true_ranges = []
        for index in range(1, len(closes)):
            high = snapshot.highs[index]
            low = snapshot.lows[index]
            previous_close = closes[index - 1]
            true_ranges.append(
                max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            )
        sample = true_ranges[-period:]
    else:
        sample = [
            abs(current - previous)
            for previous, current in zip(closes, closes[1:])
        ][-period:]

    atr = sum(sample) / len(sample) if sample else 0.0
    return max(atr, 1e-12)


def _m15_score(snapshot: MarketSnapshot) -> tuple[int, float, float, float, list[str]]:
    """Build an M15 technical score from EMA trend, RSI, momentum and ATR."""
    closes = snapshot.closes
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(snapshot, 14)

    ema_gap_atr = (ema9 - ema21) / atr14
    trend_component = _clamp(ema_gap_atr * 18.0, -45.0, 45.0)
    rsi_component = _clamp((rsi14 - 50.0) * 0.4, -20.0, 20.0)
    momentum_4 = (closes[-1] - closes[-5]) / atr14
    momentum_component = _clamp(momentum_4 * 4.0, -15.0, 15.0)

    score = int(
        round(
            _clamp(
                trend_component + rsi_component + momentum_component,
                -100.0,
                100.0,
            )
        )
    )
    reasons = [
        f"M15 EMA9={ema9:.5f}, EMA21={ema21:.5f}",
        f"M15 RSI14={rsi14:.1f}, ATR14={atr14:.5f}",
        f"M15 momentum(4 bars)={momentum_4:.2f} ATR",
        (
            "Technical components: "
            f"trend={trend_component:.1f}, RSI={rsi_component:.1f}, "
            f"momentum={momentum_component:.1f}"
        ),
    ]
    return score, rsi14, atr14, ema9 - ema21, reasons


def _direction_and_confidence(
    technical_score: int,
    rsi14: float,
    ema_gap: float,
    spread_to_atr: float,
    news_risk: NewsRisk,
) -> tuple[Action, int, list[str]]:
    """Turn the technical score into a dynamic, explainable confidence value."""
    reasons: list[str] = []
    if technical_score >= 30:
        action = Action.BUY
    elif technical_score <= -30:
        action = Action.SELL
    else:
        action = Action.WAIT

    confidence = 48.0 + min(34.0, abs(technical_score) * 0.4)

    aligned = (
        action is Action.BUY and ema_gap > 0.0 and rsi14 >= 52.0
    ) or (
        action is Action.SELL and ema_gap < 0.0 and rsi14 <= 48.0
    )
    if aligned:
        confidence += 6.0
        reasons.append("EMA direction and RSI confirm the same M15 bias.")

    if spread_to_atr <= 0.10:
        confidence += 2.0
    elif spread_to_atr > 0.25:
        confidence -= 15.0
        reasons.append("Spread is large relative to M15 ATR.")
    elif spread_to_atr > 0.12:
        confidence -= 8.0
        reasons.append("Spread is elevated relative to M15 ATR.")

    if news_risk is NewsRisk.MEDIUM:
        confidence -= 12.0
        reasons.append("Medium-impact news reduced confidence.")

    return action, int(round(_clamp(confidence, 0.0, 100.0))), reasons


def _tipranks_adjustment(
    action: Action,
    context: TipRanksContext | None,
) -> tuple[int, str, list[str]]:
    """Use TipRanks only as a small higher-timeframe confirmation layer."""
    if context is None:
        return 0, "UNAVAILABLE", [
            "TipRanks context unavailable or stale; no confidence adjustment."
        ]

    bias = 0
    components: list[str] = []

    if context.change_percentage is not None:
        if context.change_percentage >= 0.10:
            bias += 2
        elif context.change_percentage <= -0.10:
            bias -= 2
        components.append(f"day change={context.change_percentage:+.2f}%")

    if context.price_avg_50 is not None:
        above_50 = context.price >= context.price_avg_50
        bias += 2 if above_50 else -2
        components.append("above 50D avg" if above_50 else "below 50D avg")

    if context.price_avg_200 is not None:
        above_200 = context.price >= context.price_avg_200
        bias += 2 if above_200 else -2
        components.append("above 200D avg" if above_200 else "below 200D avg")

    detail = ", ".join(components) if components else "no directional fields"
    if action is Action.WAIT or bias == 0:
        return 0, "NEUTRAL", [
            f"TipRanks context neutral for M15 decision ({detail})."
        ]

    aligned = (action is Action.BUY and bias > 0) or (
        action is Action.SELL and bias < 0
    )
    magnitude = min(6, abs(bias))
    adjustment = magnitude if aligned else -magnitude
    direction = "confirmed" if aligned else "opposed"
    status = "CONFIRM" if aligned else "OPPOSE"
    return adjustment, status, [
        f"TipRanks higher-timeframe context {direction} M15 bias "
        f"({detail}); confidence adjustment={adjustment:+d}."
    ]


def build_hint(
    snapshot: MarketSnapshot,
    news: list[NewsItem],
    max_risk_percent: float,
    tipranks_context: TipRanksContext | None = None,
) -> TradeHint:
    """Build a dynamic M15-first hint with news and optional TipRanks context."""
    technical_score, rsi14, atr14, ema_gap, reasons = _m15_score(snapshot)
    spread = max(0.0, snapshot.ask - snapshot.bid)
    spread_to_atr = spread / atr14
    news_risk = risk_for_symbol(snapshot.symbol, news)
    reasons.extend(
        [
            f"Current spread={spread:.5f} ({spread_to_atr:.2f} ATR)",
            f"News risk={news_risk.value}",
        ]
    )

    tipranks_status = "BYPASSED"
    tipranks_adjustment = 0
    timeframe = snapshot.timeframe.upper()
    if timeframe not in M15_TIMEFRAMES:
        action = Action.WAIT
        confidence = 55
        reasons.append(
            f"M15-first engine received {snapshot.timeframe}; "
            "new entries are disabled outside M15."
        )
    elif news_risk is NewsRisk.HIGH:
        action = Action.WAIT
        confidence = 85
        reasons.append("High-impact news gate blocked new entries.")
    else:
        action, confidence, confidence_reasons = _direction_and_confidence(
            technical_score,
            rsi14,
            ema_gap,
            spread_to_atr,
            news_risk,
        )
        reasons.extend(confidence_reasons)

        tipranks_adjustment, tipranks_status, tipranks_reasons = _tipranks_adjustment(
            action,
            tipranks_context,
        )
        confidence = int(
            round(_clamp(confidence + tipranks_adjustment, 0.0, 100.0))
        )
        reasons.extend(tipranks_reasons)

        if action in {Action.BUY, Action.SELL} and confidence < MIN_ACTION_CONFIDENCE:
            reasons.append(
                f"Confidence {confidence} is below safety threshold "
                f"{MIN_ACTION_CONFIDENCE}; action changed to WAIT."
            )
            action = Action.WAIT
        elif action is Action.WAIT:
            reasons.append(
                "M15 technical score is inside the neutral zone (-30 to +30)."
            )

    currencies = {snapshot.symbol[:3].upper(), snapshot.symbol[3:6].upper()}
    relevant = sorted(
        (item for item in news if item.impact_score > 0 and item.currencies & currencies),
        key=lambda item: item.impact_score,
        reverse=True,
    )[:5]

    return TradeHint(
        action=action,
        symbol=snapshot.symbol,
        confidence=confidence,
        technical_score=technical_score,
        news_risk=news_risk,
        tipranks_status=tipranks_status,
        tipranks_adjustment=tipranks_adjustment,
        reasons=reasons,
        relevant_news=relevant,
        max_risk_percent=max_risk_percent,
        generated_at=datetime.now(timezone.utc),
    )
