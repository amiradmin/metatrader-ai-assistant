# FAST_SCALP_M1

`FAST_SCALP_M1` is an independent M1 scalping profile. It does not replace or modify the existing M15 strategy.

## Default safety profile

- Symbol: `XAUUSD_o`
- Signal timeframe: M1 completed candles
- Trend confirmation: M5
- Maximum simultaneous positions: **2 on hedging accounts**
- Effective maximum on netting accounts: **1**
- Risk per trade: **0.25% of equity**
- Nominal concurrent risk with two trades: approximately **0.50%** before broker minimum-lot effects
- Daily loss ceiling: **1.0%**
- Minimum confidence: **72/100**
- Maximum spread: `0.18 ATR` at the Python gate and `35` broker points at the EA gate
- Entry cooldown: 30 seconds
- Maximum one new entry per M1 candle
- Initial reward/risk target: `1.50R`
- Snapshot stale threshold: 5 seconds
- New orders are hard-blocked on real/contest accounts; execution is demo-only by design

The account-wide `PositionsTotal()` count is used as an additional conservative gate. An open M15 position therefore also consumes the fast-scalp position budget instead of letting the M1 EA silently stack extra account risk.

## Signal inputs

The Python engine combines:

- EMA 5 / 9 / 20 alignment on M1
- RSI 7
- ATR 14
- three-bar M1 momentum
- current candle body/range
- tick-volume impulse relative to the previous 20 completed M1 bars
- M5 EMA 5 / EMA 20 trend confirmation
- spread relative to M1 ATR
- news risk and news-source coverage

A directional M1 setup is vetoed when M5 explicitly points in the opposite direction.

## Failure policy

The profile fails closed for market/execution conditions that can make an M1 fill unsafe:

- stale or invalid MT5 snapshot -> no hint / no trade
- wrong timeframe -> `WAIT`
- position limit reached -> `WAIT`
- abnormal spread -> `WAIT`
- daily risk unavailable or exhausted -> `WAIT`
- high-impact news -> `WAIT`
- M5 trend opposes the M1 direction -> `WAIT`

External news-source timeouts are different: partial/unavailable coverage reduces confidence instead of automatically killing every setup. The fast endpoint caches news for 30 seconds and has a short external-data latency budget so repeated M1 polling does not wait on slow RSS/calendar providers.

## API

The existing M15 endpoint remains:

```text
GET /hint
```

The new independent endpoint is:

```text
GET /fast-scalp/hint
```

Example response fields include:

```text
profile: FAST_SCALP_M1
action: BUY | SELL | WAIT
confidence: 0..100
technical_score: -100..100
trend_m5: BULLISH | BEARISH | NEUTRAL | UNAVAILABLE
momentum_m1: STRONG | NORMAL | WEAK
risk_guard_status: OK | ...
positions_total: ...
max_open_positions: 2
max_risk_percent: 0.25
entry_ttl_seconds: 90
```

## MT5 setup

1. Copy `mt5/FastScalpM1.mq5` into the terminal's `MQL5/Experts` directory.
2. Compile it with MetaEditor.
3. In MT5, allow WebRequest for:

```text
http://127.0.0.1:8000
```

4. Put the real terminal `MQL5/Files` location into `.env`:

```text
FAST_SCALP_SNAPSHOT_PATH=/.../MQL5/Files/fast_scalp_m1_snapshot.json
```

5. Start/restart the FastAPI service so `/fast-scalp/hint` is available.
6. Open `XAUUSD_o` on **M1**.
7. Attach `FastScalpM1` to that M1 chart.
8. Use a demo account while validating the strategy.

The EA refuses to initialize on a non-M1 chart or a symbol different from its configured `TradeSymbol`.

## Validation status

The initial parameters are engineering defaults, not evidence of profitability. Before increasing the position cap or using a live account, validate expectancy, profit factor, drawdown, spread/slippage sensitivity, and consecutive-loss behavior with exported M1 history and demo forward testing.

M1 performance is particularly sensitive to spread, execution delay, broker stop levels, and intrabar path. Historical bar-level results must not be treated as a guarantee of live results.
