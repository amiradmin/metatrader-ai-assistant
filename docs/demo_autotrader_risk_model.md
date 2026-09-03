# DemoAutoTrader risk model

`mt5/DemoAutoTrader.mq5` is DEMO-only and hard-blocks real/contest accounts.

## Entry gate

A new order still requires all of the existing gates:

- API action is `BUY` or `SELL`
- confidence is at least `MinConfidence` (default 75)
- news risk is not `HIGH`
- spread is at or below `MaxSpreadPoints`
- Algo Trading is enabled
- API symbol matches the chart symbol
- managed open positions are below `MaxOpenTrades`

## Position sizing

The default sizing is now risk-based instead of a fixed 0.01 lot.

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
```

## Logging

With `VerboseLogging=true`, the Experts tab reports the signal gate and the planned order, including stop source, stop distance, volume, risk budget, approximate planned loss, SL and TP.

Example:

```text
DemoAutoTrader plan: action=BUY stop_source=ATR+SWING stop_points=... RR=2.00 volume=... risk_budget=$... planned_loss~$... SL=... TP=...
```

This logic is intended for demo forward-testing. It does not make confidence a probability of winning and does not make the strategy profitable by itself; expectancy must still be measured over a meaningful sample of trades.
