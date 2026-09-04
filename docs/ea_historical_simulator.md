# Unified EA historical simulator

This workflow replays the current MetaTraderAI strategy on real M15 OHLC/spread
history exported by the broker's MT5 terminal. It is designed for research and
demo validation; it never changes the live/demo EA automatically.

## 1. Export real broker M15 data

Compile and run `mt5/HistoricalCsvExporter.mq5` as an MT5 **Script** on
`XAUUSD_o` / M15.

Recommended inputs:

```text
InputSymbol = XAUUSD_o
InputTimeframe = PERIOD_M15
InputBars = 50000
IncludeCurrentBar = false
OutputFile = xauusd_m15_history.csv
```

The result is written to the active terminal's `MQL5/Files` directory.
Increase `InputBars` later when you want more historical days.

## 2. Run the current EA simulator

First locate the exported file:

```bash
HISTORY=$(find "$HOME/.mt5" -type f -path '*/MQL5/Files/xauusd_m15_history.csv' -printf '%T@ %p\n' \
  | sort -nr | head -n1 | cut -d' ' -f2-)
echo "$HISTORY"
```

Test the most recent 20 trading days:

```bash
uv run python -m meta_trader_ai.ea_simulator "$HISTORY" --days 20
```

Then test larger samples without changing code:

```bash
uv run python -m meta_trader_ai.ea_simulator "$HISTORY" --days 60
uv run python -m meta_trader_ai.ea_simulator "$HISTORY" --days 180
```

Or use explicit broker dates:

```bash
uv run python -m meta_trader_ai.ea_simulator "$HISTORY" \
  --from-date 2026-01-01 --to-date 2026-08-31
```

Default simulator settings mirror the unified EA:

- minimum confidence 75
- risk 0.5% per trade
- reward/risk 2.0
- max one open position
- max spread 50 points
- spread <= 0.25 ATR
- daily loss ceiling 1.5%
- ATR(14) x 1.5 stop with confirmed M15 swing expansion
- anti-chase max extension 1.5 ATR
- pullback zone 0.35 ATR, four-bar window
- daily research goal $10 on a $1000 starting balance

Outputs:

```text
data/ea_simulator_trades.csv
data/ea_simulator_daily.csv
data/ea_simulator_report.txt
```

The trade journal shows every simulated BUY/SELL, entry type, confidence,
SL/TP, stop source, spread, risk money, R result, dollar result and balance.
The daily file shows realized P/L per broker trading day and whether the $10
goal was hit.

## 3. Walk-forward learning

This is the safe learning step. The learner never optimizes risk, the daily
loss ceiling, or spread safety gates. It compares a bounded set of strategy
parameters on the previous 60 trading days, then evaluates the selected set on
the next 20 days that were unseen during selection.

```bash
uv run python -m meta_trader_ai.ea_walk_forward_learning "$HISTORY"
```

Default design:

```text
TRAIN: previous 60 trading days
TEST:  next 20 unseen trading days
repeat forward through history
```

It can use a wider research grid when enough data is available:

```bash
uv run python -m meta_trader_ai.ea_walk_forward_learning "$HISTORY" --wide-grid
```

Outputs:

```text
data/ea_learning_folds.csv
data/ea_learning_oos_trades.csv
data/ea_learning_candidate.json
data/ea_learning_report.txt
```

`ea_learning_candidate.json` is **review only**. It is intentionally not read
by MetaTraderAI.mq5 and cannot silently change demo/live parameters.

## What the simulator does and does not reproduce

It does reproduce the current technical signal engine and the main unified EA
execution rules using only information available at each historical point.

It cannot reconstruct exact historical news/TipRanks state from an OHLC CSV,
and M15 candles do not reveal the exact intrabar tick path. If both SL and TP
are touched in the same M15 candle, the simulator assumes STOP first to avoid
inflating results. Recorded candle spread is used, but broker lot minimum/step,
slippage and commissions are not reconstructed in the dollar model.

For that reason, historical simulation is a research filter. Demo forward
results remain the final validation layer before considering any parameter
change.
