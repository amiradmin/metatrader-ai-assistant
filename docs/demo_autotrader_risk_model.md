# DemoAutoTrader risk model

`mt5/DemoAutoTrader.mq5` is DEMO-only and hard-blocks real/contest accounts.

## Entry gate

A new order still requires all of the existing gates:

- API action is `BUY` or `SELL`
- confidence is at least `MinConfidence` (default 75)
- news risk is not `HIGH`
- spread is at or below `MaxSpreadPoints`
- API-level spread/ATR execution quality is at or below `MAX_SPREAD_ATR_RATIO` (default 0.25 ATR)
- daily account loss budget has enough room for one more full-risk trade
- Algo Trading is enabled
- API symbol matches the chart symbol
- managed open positions are below `MaxOpenTrades`

## Account-level daily loss circuit breaker

`ReadOnlySnapshotBridge.mq5` now exports broker-day account risk metrics from MT5 deal history:

- `day_realized_pnl`: realized trading P/L for the current broker day, including profit, commission, swap and deal fees
- `day_start_balance`: reconstructed broker-day starting balance (`current balance - day realized P/L`)

The API compares broker-day start balance with current equity so unrealized drawdown is also visible to the guard.

Default limits:

```text
MAX_RISK_PERCENT = 0.5
MAX_DAILY_LOSS_PERCENT = 1.5
```

A directional hint is changed to `WAIT` when either:

1. current day drawdown is already at or above 1.5%, or
2. current day drawdown plus the maximum 0.5% risk of the next trade would exceed 1.5%.

This is intentionally conservative: it reserves enough remaining daily loss budget for the full planned stop before allowing another entry.

If the running MT5 bridge has not yet been recompiled with the new fields, the daily-loss guard reports `UNAVAILABLE` and does not invent a loss state. This keeps backward compatibility while making the missing risk telemetry explicit.

## Execution-quality hard gate

The API also measures current spread relative to completed M15 ATR. `MAX_SPREAD_ATR_RATIO=0.25` is the default hard ceiling.

If a directional signal survives the normal confidence/news checks but spread is greater than 0.25 ATR, the action is changed to `WAIT`. This complements the EA's broker-specific `MaxSpreadPoints` check with a volatility-normalized execution check.

## Position sizing

The default sizing is risk-based instead of a fixed 0.01 lot.

- `UseRiskBasedSizing=true`
- `RiskPercent=0.5`
- code enforces a hard maximum of 0.5% of current equity per signal allocation
- if `TradesPerSignal > 1`, the risk budget is divided across those entries
- `OrderCalcProfit()` estimates the money loss for one lot from entry to stop
- volume is rounded DOWN to the broker volume step so rounding cannot exceed the risk budget
- if the broker minimum lot would exceed the risk budget, the trade is skipped

## Dynamic stop and target

The default stop is derived from completed M15 data only:

1. ATR(14) from the most recently completed M15 candle, multiplied by `AtrMultiplier` (default 1.5)
2. the most recent confirmed M15 fractal swing (2 bars on each side by default), plus `StructureBufferPoints`
3. the wider valid distance is used, subject to broker stop-level rules and `MaxStopPoints`

The target is then set from the actual stop distance using `RewardRiskRatio` (default 2.0), so the default planned RR remains 1:2.

If a detected swing is farther than `MaxStopPoints`, it is logged as observational and ATR remains the stop source. If ATR itself requires a stop wider than `MaxStopPoints`, the entry is skipped instead of silently taking an oversized stop.

## Default inputs

```text
MinConfidence = 75
UseRiskBasedSizing = true
RiskPercent = 0.5
FallbackLotSize = 0.01
UseDynamicStop = true
AtrPeriod = 14
AtrMultiplier = 1.50
SwingLookbackBars = 30
SwingLeftBars = 2
SwingRightBars = 2
StructureBufferPoints = 50
MinStopPoints = 150
MaxStopPoints = 1200
RewardRiskRatio = 2.0
MaxSpreadPoints = 50
MAX_DAILY_LOSS_PERCENT = 1.5
MAX_SPREAD_ATR_RATIO = 0.25
```

## Logging and API visibility

The `/hint` payload now exposes:

- `risk_guard_status`
- `day_drawdown_percent`
- `spread_to_atr`

The `reasons` list explains exactly why an entry was allowed or changed to `WAIT`.

With `VerboseLogging=true`, the Experts tab continues to report the signal gate and the planned order, including stop source, stop distance, volume, risk budget, approximate planned loss, SL and TP.

This logic is intended for demo forward-testing. It does not make confidence a probability of winning and does not make the strategy profitable by itself; expectancy must still be measured over a meaningful sample of trades.
