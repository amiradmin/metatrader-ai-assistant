"""Pre-trade risk and execution-quality controls.

These controls do not create direction. They can only preserve or block an
existing BUY/SELL hint when account risk, execution quality, or higher-
timeframe confirmation is unacceptable.
"""

from meta_trader_ai.models import Action, MarketSnapshot, TradeHint


DIRECTIONAL_ACTIONS = {Action.BUY, Action.SELL}


def _atr(snapshot: MarketSnapshot, period: int = 14) -> float:
    """Return recent ATR using completed OHLC, with close-to-close fallback."""
    closes = snapshot.closes
    has_ohlc = (
        len(snapshot.highs) == len(closes)
        and len(snapshot.lows) == len(closes)
        and len(closes) >= period + 1
    )
    if has_ohlc:
        true_ranges: list[float] = []
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


def _daily_drawdown_percent(snapshot: MarketSnapshot) -> float | None:
    """Return account drawdown from broker-day start balance to current equity."""
    if snapshot.day_start_balance is None or snapshot.day_start_balance <= 0.0:
        return None
    drawdown = (
        (snapshot.day_start_balance - snapshot.equity)
        / snapshot.day_start_balance
        * 100.0
    )
    return max(0.0, drawdown)


def _block_directional(hint: TradeHint, reason: str) -> None:
    """Fail closed without ever manufacturing a new direction."""
    if hint.action in DIRECTIONAL_ACTIONS:
        hint.action = Action.WAIT
        hint.reasons.append(reason)


def _apply_strict_setup_gate(hint: TradeHint) -> None:
    """Require all mandatory non-optional entry gates before BUY/SELL survives.

    The gate deliberately fails closed.  A directional M15 setup is only
    executable when the account risk guard is healthy and H1/H4 structure both
    confirm the same direction.  TipRanks remains optional context, but an
    explicit OPPOSE result is a veto.  News-source outages still follow the
    existing degraded-mode policy: UNKNOWN/PARTIAL reduce confidence rather
    than becoming an automatic hard stop.
    """
    if hint.action not in DIRECTIONAL_ACTIONS:
        return

    if hint.risk_guard_status != "OK":
        _block_directional(
            hint,
            "Strict entry gate blocked direction because the account risk guard "
            f"is {hint.risk_guard_status}, not OK.",
        )
        return

    if hint.mtf_status != "CONFIRM":
        _block_directional(
            hint,
            "Strict entry gate blocked direction because H1/H4 structure does "
            f"not fully confirm M15 (MTF={hint.mtf_status}).",
        )
        return

    if hint.tipranks_status == "OPPOSE":
        _block_directional(
            hint,
            "Strict entry gate blocked direction because TipRanks higher-"
            "timeframe context explicitly opposes the M15 setup.",
        )


def apply_pretrade_controls(
    snapshot: MarketSnapshot,
    hint: TradeHint,
    *,
    max_daily_loss_percent: float,
    max_spread_atr_ratio: float,
) -> TradeHint:
    """Apply account, spread and strict setup gates before execution.

    The daily guard is conservative: a new directional trade is blocked when
    the current day drawdown plus the configured maximum per-trade risk could
    breach the daily loss ceiling.  After those controls, the strict setup gate
    requires a healthy risk guard and full H1/H4 confirmation.  Therefore BUY
    or SELL is never returned merely because the raw M15 score is directional.
    """
    spread = max(0.0, snapshot.ask - snapshot.bid)
    spread_to_atr = spread / _atr(snapshot)
    hint.spread_to_atr = spread_to_atr
    hint.reasons.append(
        f"Execution quality: spread={spread_to_atr:.2f} ATR; "
        f"hard limit={max_spread_atr_ratio:.2f} ATR."
    )

    day_drawdown = _daily_drawdown_percent(snapshot)
    hint.day_drawdown_percent = day_drawdown

    if max_daily_loss_percent <= 0.0:
        hint.risk_guard_status = "DISABLED"
    elif day_drawdown is None:
        hint.risk_guard_status = "UNAVAILABLE"
        hint.reasons.append(
            "Daily-loss guard unavailable because broker-day start balance "
            "was not present in the MT5 snapshot."
        )
    else:
        realized = snapshot.day_realized_pnl
        realized_detail = (
            f"; realized P/L={realized:+.2f}"
            if realized is not None
            else ""
        )
        projected_drawdown = day_drawdown + max(0.0, hint.max_risk_percent)
        if day_drawdown >= max_daily_loss_percent:
            hint.risk_guard_status = "DAILY_LOSS_LIMIT"
            hint.reasons.append(
                f"Daily loss circuit breaker: drawdown={day_drawdown:.2f}% "
                f">= limit={max_daily_loss_percent:.2f}%{realized_detail}."
            )
            _block_directional(hint, "Directional entry blocked by daily loss limit.")
        elif (
            hint.action in DIRECTIONAL_ACTIONS
            and projected_drawdown > max_daily_loss_percent
        ):
            hint.risk_guard_status = "DAILY_RISK_BUDGET_EXHAUSTED"
            hint.reasons.append(
                f"Daily risk budget exhausted: current drawdown={day_drawdown:.2f}% "
                f"+ next-trade risk ceiling={hint.max_risk_percent:.2f}% "
                f"would exceed {max_daily_loss_percent:.2f}%{realized_detail}."
            )
            _block_directional(
                hint,
                "Directional entry blocked because the next trade could breach "
                "the daily risk budget.",
            )
        else:
            hint.risk_guard_status = "OK"
            hint.reasons.append(
                f"Daily risk guard OK: drawdown={day_drawdown:.2f}%, "
                f"projected worst-case={projected_drawdown:.2f}% / "
                f"{max_daily_loss_percent:.2f}%."
            )

    if (
        hint.action in DIRECTIONAL_ACTIONS
        and max_spread_atr_ratio > 0.0
        and spread_to_atr > max_spread_atr_ratio
    ):
        _block_directional(
            hint,
            f"Abnormal spread gate blocked entry: {spread_to_atr:.2f} ATR "
            f"> {max_spread_atr_ratio:.2f} ATR.",
        )

    _apply_strict_setup_gate(hint)
    return hint
