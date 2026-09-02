# MetaTrader AI Assistant

A demo-first MetaTrader 5 market monitoring and decision-support project.

## Goal

Collect market and demo-account data from MetaTrader 5, calculate transparent indicators and risk metrics, and present decision-support hints in a local dashboard.

## Safety defaults

- Demo accounts only
- Read-only integration in the first milestone
- No automatic order execution
- No credentials committed to Git
- Maximum risk and loss limits must be configured explicitly
- Every hint includes invalidation conditions and risk context

## Planned architecture

```text
MetaTrader 5 -> Local bridge -> Python analysis API -> Dashboard -> Human confirmation
```

## Initial milestones

1. Install MetaTrader 5 and create a demo account.
2. Connect a local read-only bridge.
3. Stream ticks, candles, account state, positions, and orders.
4. Add RSI, EMA, MACD, ATR, spread, and position-risk calculations.
5. Build a local dashboard and trading journal.
6. Add AI-assisted explanations and alerts.
7. Backtest and paper-trade before considering any execution feature.

## Status

Bootstrap in progress. Do not use this project with real funds.
