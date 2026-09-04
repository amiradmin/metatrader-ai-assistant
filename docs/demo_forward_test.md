# Real demo forward test

This workflow measures the strategy that actually sends orders through `DemoAutoTrader.mq5` on an MT5 **demo account**. It is separate from `shadow_forward.py`, which remains a hypothetical frozen-candidate experiment.

## What is collected

Two journals are intentionally kept separate:

1. `forward_journal.csv` records one `/hint` decision per symbol per M15 bucket, including confidence, technical score, news coverage, daily risk guard, spread/ATR, H1/H4 structure and TipRanks observer state.
2. `demo_trade_journal.csv` is generated inside MT5 from actual closed positions opened with the DemoAutoTrader magic number. It records the real entry/exit, initial SL/TP, broker P/L including commission/swap/fee, initial risk money and realized `pnl_r`.

This makes it possible to correlate *why a trade was allowed* with *what actually happened after execution* without changing the trading rules during the sample.

## MT5 setup

Compile and attach these EAs:

- `ReadOnlySnapshotBridge.mq5` — fresh M15 snapshot + H1/H4 context + broker-day risk metrics.
- `ReadOnlySignalPanel.mq5` — display only.
- `DemoAutoTrader.mq5` — DEMO-only execution; default max risk 0.5% and RR 1:2.
- `DemoTradeJournal.mq5` — DEMO-only history observer; contains no order functions.

`DemoTradeJournal` defaults must match the trader:

```text
MagicNumber = 26090315
InputSymbol = XAUUSD_o
HistoryDays = 180
RefreshSeconds = 30
OutputFile = demo_trade_journal.csv
```

The journal is written under the active terminal's `MQL5/Files` directory.

## Python setup

Point `.env` to the MT5-generated journal, for example:

```text
DEMO_TRADE_JOURNAL_PATH=/home/amir/.mt5/drive_c/users/amir/AppData/Roaming/MetaQuotes/Terminal/<TERMINAL_ID>/MQL5/Files/demo_trade_journal.csv
```

Keep the API running:

```bash
uv run uvicorn meta_trader_ai.api:app --reload
```

In another terminal, record every M15 decision:

```bash
uv run python -m meta_trader_ai.forward_logger --output data/forward_journal.csv
```

At any time, print KPIs from the trades that actually closed on the demo account:

```bash
uv run python -m meta_trader_ai.demo_kpi_report
```

The report writes `data/demo_kpi_latest.txt` and shows:

- closed trade count
- win rate
- profit factor
- expectancy in R/trade
- net R
- max drawdown in R
- actual net account P/L recorded by MT5
- bootstrap expectancy interval after enough trades exist

## Forward-test discipline

Keep parameters frozen during the initial evidence window. Do not raise risk because of a short winning streak and do not optimize after a few losses.

Suggested review milestones:

```text
<10 trades   startup only
10-29        very early
30-59        early evidence
60-99        moderate sample
100+         stronger sample, still not a guarantee
```

The primary decision metric is forward expectancy (`R/trade`). Profit factor and drawdown are supporting metrics. Real-demo results are evidence about this implementation and broker environment; they do not guarantee future live-account profitability.
