# Observer-only Market Context Layer

This layer enriches the shadow forward-test journal without changing `/hint`, `DemoAutoTrader`, `ManualConfirmTrader`, or the frozen shadow eligibility rule.

## What it records

- Completed H1 trend and volatility regime
- Completed H4 trend and volatility regime
- US 10Y real yield from FRED series `DFII10`
- One-observation real-yield change in basis points
- Upcoming market-moving BLS releases from the official BLS calendar
- Scheduled FOMC policy-decision markers

The data is written to `data/shadow_context.csv`. External responses are cached under `data/market_context_cache.json`. Both are ignored by Git because the whole `data/` directory is ignored.

## MT5 setup

Compile and attach `mt5/ReadOnlyMarketContextBridge.mq5` to one MT5 chart.

Recommended inputs:

```text
InputSymbol = XAUUSD_o
InputBars = 100
InputIntervalSeconds = 15
OutputFile = mt5_context.json
```

The EA has no order functions. It exports completed H1 and H4 candles only (`CopyRates` starts at shift 1).

By default Python expects `mt5_context.json` next to the configured `MT5_SNAPSHOT_PATH`, so no extra `.env` setting is normally required.

## Verify the context layer

```bash
uv run python -m meta_trader_ai.context_status
```

A healthy output should show non-`UNAVAILABLE` H1/H4 regimes plus the latest available real yield and the next relevant economic event. A temporary external-source failure is reported as a warning and does not stop the shadow tester.

## Run shadow forward testing

```bash
uv run python -m meta_trader_ai.shadow_forward \
  --point-size 0.01 \
  --sl-points 300 \
  --tp-points 600
```

The original frozen shadow strategy remains:

```text
BUY only
M15 trend aligned
M15 LOW_VOLATILITY
1.50 <= momentum(4) < 2.00 ATR
SL = 300 points
TP = 600 points
one shadow position at a time
```

H1/H4, real yield, and calendar fields are observer-only. Do not use them as filters until enough forward data exists to test their incremental value without tuning the current forward sample.
