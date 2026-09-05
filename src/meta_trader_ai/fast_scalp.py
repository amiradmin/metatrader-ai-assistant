"""Fast M1 scalping signal engine for XAUUSD-style instruments.

The module is intentionally separate from the M15 engine. It uses completed M1
candles for direction, M5 closes as a trend filter, and conservative execution
risk gates. It never places orders by itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from meta_trader_ai.models import Action, NewsCoverage, NewsItem, NewsRisk
from meta_trader_ai.news import risk_for_symbol

M1_TIMEFRAMES = {"M1", "PERIOD_M1"}
DIRECTIONAL_ACTIONS = {Action.BUY, Action.SELL}


class FastScalpSnapshot(BaseModel):
    """Snapshot exported by the dedicated M1 fast-scalp EA."""

    symbol: str
    timeframe: str
    generated_at: datetime
    bid: float
    ask: float
    balance: float = Field(gt=0)
    equity: float = Field(gt=0)
    positions_total: int = Field(default=0, ge=0)
    day_start_balance: float | None = Field(default=None, gt=0)
    day_realized_pnl: float | None = None
    opens: list[float] = Field(min_length=30)
    highs: list[float] = Field(min_length=30)
    lows: list[float] = Field(min_length=30)
    closes: list[float] = Field(min_length=30)
    tick_volumes: list[int] = Field(default_factory=list)
    m5_closes: list[float] = Field(min_length=20)


class FastScalpHint(BaseModel):
    """Explainable M1 scalp decision consumed by the demo execution EA."""

    profile: Literal["FAST_SCALP_M1"] = "FAST_SCALP_M1"
    action: Action
    symbol: str
    confidence: int = Field(ge=0, le=100)
    technical_score: int = Field(ge=-100, le=100)
    trend_m5: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNAVAILABLE"]
    momentum_m1: Literal["STRONG", "NORMAL", "WEAK"]
    news_risk: NewsRisk
    news_coverage: NewsCoverage = NewsCoverage.COMPLETE
    failed_news_sources: int = Field(default=0, ge=0)
    risk_guard_status: str
    day_drawdown_percent: float | None = Field(default=None, ge=0)
    spread_to_atr: float = Field(default=0.0, ge=0)
    positions_total: int = Field(default=0, ge=0)
    max_open_positions: int = Field(ge=1)
    max_risk_percent: float = Field(gt=0)
    entry_ttl_seconds: int = Field(gt=0)
    reasons: list[str]
    relevant_news: list[NewsItem]
    generated_at: datetime


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(values: list[float], period: int = 7) -> float:
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    sample = deltas[-period:]
    if not sample:
        return 50.0
    gains = sum(max(delta, 0.0) for delta in sample) / len(sample)
    losses = sum(max(-delta, 0.0) for delta in sample) / len(sample)
    if gains == 0.0 and losses == 0.0:
        return 50.0
    if losses == 0.0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(snapshot: FastScalpSnapshot, period: int = 14) -> float:
    true_ranges: list[float] = []
    for index in range(1, len(snapshot.closes)):
        high = snapshot.highs[index]
        low = snapshot.lows[index]
        previous_close = snapshot.closes[index - 1]
        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    sample = true_ranges[-period:]
    atr = sum(sample) / len(sample) if sample else 0.0
    return max(atr, 1e-12)


def _m5_trend(snapshot: FastScalpSnapshot) -> str:
    closes = snapshot.m5_closes
    if len(closes) < 20:
        return "UNAVAILABLE"
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)
    gap = ema5 - ema20
    if abs(gap) <= max(abs(closes[-1]) * 0.00001, 1e-12):
        return "NEUTRAL"
    return "BULLISH" if gap > 0 else "BEARISH"


def _volume_component(snapshot: FastScalpSnapshot) -> tuple[float, str]:
    volumes = snapshot.tick_volumes
    if len(volumes) < 21:
        return 0.0, "M1 tick volume unavailable; volume impulse was skipped."
    baseline = sum(volumes[-21:-1]) / 20.0
    current = volumes[-1]
    if baseline <= 0:
        return 0.0, "M1 tick-volume baseline is zero; volume impulse was skipped."
    ratio = current / baseline
    if ratio >= 1.35:
        return 6.0, f"M1 tick-volume impulse={ratio:.2f}x baseline."
    if ratio <= 0.65:
        return -5.0, f"M1 tick volume is thin at {ratio:.2f}x baseline."
    return 0.0, f"M1 tick volume is normal at {ratio:.2f}x baseline."


def _technical_score(
    snapshot: FastScalpSnapshot,
) -> tuple[int, float, float, str, str, list[str]]:
    closes = snapshot.closes
    ema5 = _ema(closes, 5)
    ema9 = _ema(closes, 9)
    ema20 = _ema(closes, 20)
    rsi7 = _rsi(closes, 7)
    atr14 = _atr(snapshot, 14)

    if ema5 > ema9 > ema20:
        trend_component = 32.0
    elif ema5 < ema9 < ema20:
        trend_component = -32.0
    else:
        trend_component = _clamp((ema5 - ema20) / atr14 * 18.0, -20.0, 20.0)

    momentum3 = (closes[-1] - closes[-4]) / atr14
    momentum_component = _clamp(momentum3 * 18.0, -24.0, 24.0)

    if 53.0 <= rsi7 <= 72.0:
        rsi_component = 14.0
    elif 28.0 <= rsi7 <= 47.0:
        rsi_component = -14.0
    elif rsi7 > 82.0:
        rsi_component = -8.0
    elif rsi7 < 18.0:
        rsi_component = 8.0
    else:
        rsi_component = _clamp((rsi7 - 50.0) * 0.45, -10.0, 10.0)

    candle_range = max(snapshot.highs[-1] - snapshot.lows[-1], 1e-12)
    candle_body = snapshot.closes[-1] - snapshot.opens[-1]
    body_ratio = candle_body / candle_range
    candle_component = _clamp(body_ratio * 10.0, -10.0, 10.0)

    volume_strength, volume_reason = _volume_component(snapshot)
    if volume_strength != 0.0:
        direction = 1.0 if momentum3 >= 0 else -1.0
        volume_component = abs(volume_strength) * direction if volume_strength > 0 else volume_strength
    else:
        volume_component = 0.0

    m5_trend = _m5_trend(snapshot)
    m5_component = 0.0
    if m5_trend == "BULLISH":
        m5_component = 16.0
    elif m5_trend == "BEARISH":
        m5_component = -16.0

    raw = (
        trend_component
        + momentum_component
        + rsi_component
        + candle_component
        + volume_component
        + m5_component
    )
    score = int(round(_clamp(raw, -100.0, 100.0)))

    momentum_label = "STRONG" if abs(momentum3) >= 0.55 else "NORMAL"
    if abs(momentum3) < 0.18:
        momentum_label = "WEAK"

    reasons = [
        f"M1 EMA5={ema5:.5f}, EMA9={ema9:.5f}, EMA20={ema20:.5f}.",
        f"M1 RSI7={rsi7:.1f}, ATR14={atr14:.5f}, momentum(3)={momentum3:.2f} ATR.",
        f"M1 candle body/range={body_ratio:.2f}; M5 trend={m5_trend}.",
        volume_reason,
        (
            "Scalp components: "
            f"trend={trend_component:.1f}, momentum={momentum_component:.1f}, "
            f"RSI={rsi_component:.1f}, candle={candle_component:.1f}, "
            f"volume={volume_component:.1f}, M5={m5_component:.1f}."
        ),
    ]
    return score, rsi7, atr14, m5_trend, momentum_label, reasons


def _drawdown_percent(snapshot: FastScalpSnapshot) -> float | None:
    if snapshot.day_start_balance is None or snapshot.day_start_balance <= 0:
        return None
    return max(
        0.0,
        (snapshot.day_start_balance - snapshot.equity)
        / snapshot.day_start_balance
        * 100.0,
    )


def _relevant_news(symbol: str, news: list[NewsItem]) -> list[NewsItem]:
    currencies = {symbol[:3].upper(), symbol[3:6].upper()}
    return sorted(
        (item for item in news if item.impact_score > 0 and item.currencies & currencies),
        key=lambda item: item.impact_score,
        reverse=True,
    )[:5]


def build_fast_scalp_hint(
    snapshot: FastScalpSnapshot,
    news: list[NewsItem],
    *,
    max_risk_percent: float = 0.25,
    max_open_positions: int = 2,
    max_daily_loss_percent: float = 1.0,
    max_spread_atr_ratio: float = 0.18,
    min_confidence: int = 72,
    entry_ttl_seconds: int = 90,
    news_coverage: NewsCoverage = NewsCoverage.COMPLETE,
    failed_news_sources: int = 0,
) -> FastScalpHint:
    """Build one guarded FAST_SCALP_M1 decision from completed candles."""
    if max_open_positions < 1:
        raise ValueError("max_open_positions must be at least 1")
    if not 0.0 < max_risk_percent <= 0.5:
        raise ValueError("max_risk_percent must be > 0 and <= 0.5")

    score, _rsi7, atr14, m5_trend, momentum_label, reasons = _technical_score(snapshot)
    spread = max(0.0, snapshot.ask - snapshot.bid)
    spread_to_atr = spread / atr14

    news_risk = (
        NewsRisk.UNKNOWN
        if news_coverage is NewsCoverage.UNAVAILABLE
        else risk_for_symbol(snapshot.symbol, news)
    )

    if score >= 35:
        action = Action.BUY
    elif score <= -35:
        action = Action.SELL
    else:
        action = Action.WAIT

    confidence = 48.0 + min(38.0, abs(score) * 0.42)
    if momentum_label == "STRONG":
        confidence += 4.0
    elif momentum_label == "WEAK":
        confidence -= 5.0

    aligned_m5 = (
        action is Action.BUY and m5_trend == "BULLISH"
    ) or (
        action is Action.SELL and m5_trend == "BEARISH"
    )
    opposed_m5 = (
        action is Action.BUY and m5_trend == "BEARISH"
    ) or (
        action is Action.SELL and m5_trend == "BULLISH"
    )
    if action in DIRECTIONAL_ACTIONS and aligned_m5:
        confidence += 5.0
        reasons.append("M5 trend confirms the M1 scalp direction.")
    elif action in DIRECTIONAL_ACTIONS and opposed_m5:
        confidence -= 18.0
        reasons.append("M5 trend opposes the M1 scalp direction.")

    if spread_to_atr <= 0.08:
        confidence += 2.0
    elif spread_to_atr > max_spread_atr_ratio:
        confidence -= 20.0
    elif spread_to_atr > 0.12:
        confidence -= 8.0

    if news_risk is NewsRisk.MEDIUM:
        confidence -= 12.0
        reasons.append("Medium-impact news reduced fast-scalp confidence.")
    elif news_risk is NewsRisk.UNKNOWN:
        confidence -= 16.0
        reasons.append("News coverage is unavailable; confidence was reduced.")
    if news_coverage is NewsCoverage.PARTIAL:
        confidence -= 2.0
        reasons.append("Partial news coverage applied a small confidence penalty.")

    confidence = int(round(_clamp(confidence, 0.0, 100.0)))
    day_drawdown = _drawdown_percent(snapshot)
    risk_status = "OK"

    if snapshot.timeframe.upper() not in M1_TIMEFRAMES:
        action = Action.WAIT
        risk_status = "WRONG_TIMEFRAME"
        reasons.append(
            f"FAST_SCALP_M1 requires M1, but snapshot timeframe is {snapshot.timeframe}."
        )
    elif news_risk is NewsRisk.HIGH:
        action = Action.WAIT
        risk_status = "HIGH_IMPACT_NEWS"
        reasons.append("High-impact news hard-blocked new scalp entries.")
    elif snapshot.positions_total >= max_open_positions:
        action = Action.WAIT
        risk_status = "POSITION_LIMIT"
        reasons.append(
            f"Open-position cap reached: {snapshot.positions_total}/{max_open_positions}."
        )
    elif max_spread_atr_ratio > 0 and spread_to_atr > max_spread_atr_ratio:
        action = Action.WAIT
        risk_status = "SPREAD_TOO_WIDE"
        reasons.append(
            f"Spread gate blocked entry: {spread_to_atr:.2f} ATR > "
            f"{max_spread_atr_ratio:.2f} ATR."
        )
    elif day_drawdown is None:
        action = Action.WAIT
        risk_status = "DAILY_RISK_UNAVAILABLE"
        reasons.append("Broker-day start balance is unavailable; scalp entry fails closed.")
    elif day_drawdown >= max_daily_loss_percent:
        action = Action.WAIT
        risk_status = "DAILY_LOSS_LIMIT"
        reasons.append(
            f"Daily drawdown {day_drawdown:.2f}% reached the "
            f"{max_daily_loss_percent:.2f}% scalp limit."
        )
    elif day_drawdown + max_risk_percent > max_daily_loss_percent:
        action = Action.WAIT
        risk_status = "DAILY_RISK_BUDGET_EXHAUSTED"
        reasons.append(
            f"Next {max_risk_percent:.2f}% risk could breach the "
            f"{max_daily_loss_percent:.2f}% daily scalp limit."
        )
    elif action in DIRECTIONAL_ACTIONS and opposed_m5:
        action = Action.WAIT
        risk_status = "M5_OPPOSE"
        reasons.append("M5 opposition hard-blocked the M1 entry.")
    elif action in DIRECTIONAL_ACTIONS and confidence < min_confidence:
        action = Action.WAIT
        risk_status = "CONFIDENCE"
        reasons.append(
            f"Fast-scalp confidence {confidence} is below {min_confidence}."
        )
    elif action is Action.WAIT:
        risk_status = "NO_EDGE"
        reasons.append("M1 technical score is inside the scalp neutral zone.")

    reasons.append(
        f"Execution quality: spread={spread_to_atr:.2f} ATR; "
        f"position cap={snapshot.positions_total}/{max_open_positions}; "
        f"risk/trade={max_risk_percent:.2f}%."
    )

    return FastScalpHint(
        action=action,
        symbol=snapshot.symbol,
        confidence=confidence,
        technical_score=score,
        trend_m5=m5_trend,
        momentum_m1=momentum_label,
        news_risk=news_risk,
        news_coverage=news_coverage,
        failed_news_sources=failed_news_sources,
        risk_guard_status=risk_status,
        day_drawdown_percent=day_drawdown,
        spread_to_atr=spread_to_atr,
        positions_total=snapshot.positions_total,
        max_open_positions=max_open_positions,
        max_risk_percent=max_risk_percent,
        entry_ttl_seconds=entry_ttl_seconds,
        reasons=reasons,
        relevant_news=_relevant_news(snapshot.symbol, news),
        generated_at=datetime.now(timezone.utc),
    )
