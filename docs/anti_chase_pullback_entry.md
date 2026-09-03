# DemoAutoTrader anti-chase + pullback entry

This logic is demo-only and keeps the M15 API as the sole source of direction.

## Why it exists

Strong XAUUSD moves can leave price far from its completed M15 mean. Entering immediately after an extended impulse can require an impractically wide M15 ATR stop and can create poor entry quality. The anti-chase layer therefore delays execution without changing the API signal itself.

## Default timing rules

- `UseChasingFilter = true`
- `MaxExtensionAtr = 1.50`
- `PullbackZoneAtr = 0.35`
- `PullbackMaxBars = 4`

The EA reads completed M15 ATR(14), EMA9 and EMA21 values.

For BUY, extension is `(current ask - EMA21) / ATR`. For SELL it is `(EMA21 - current bid) / ATR`.

If extension is above `MaxExtensionAtr`, the EA does not chase the signal. It records a pending pullback state. The state is cleared if the API direction disappears, confidence falls below the execution threshold, high-impact news appears, the direction flips, or the pullback window expires.

A pending BUY becomes eligible only after M15 EMA9 remains above EMA21, price has reclaimed EMA9/EMA21, and price is no more than `PullbackZoneAtr` above EMA9. SELL is the mirror image.

## Precision stop after a successful pullback

- `UsePullbackPrecisionStop = true`
- `PullbackStopTimeframe = PERIOD_M5`
- `PullbackStopLookbackBars = 30`
- `PullbackStopLeftBars = 2`
- `PullbackStopRightBars = 2`
- `PullbackStopBufferPoints = 30`

When a chase-blocked setup later triggers a valid pullback/reclaim, the EA first tries to place the stop beyond the latest confirmed M5 swing plus buffer. This is intentionally separate from the normal M15 ATR stop so a precision entry can use a closer structural invalidation level.

If the M5 precision stop is unavailable or exceeds `MaxStopPoints`, the EA falls back to the existing M15 ATR + M15 swing plan. Risk-based sizing still has the final veto: if the broker minimum lot would exceed the 0.5% risk budget, the trade is skipped.

## Important

The anti-chase layer changes execution timing, not the signal direction. It does not guarantee a profitable trade and must be evaluated separately in demo forward testing before any consideration of real-account use.
