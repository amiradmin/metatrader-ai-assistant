# MetaTrader AI Assistant

A read-only-first MetaTrader 5 assistant for Ubuntu. MetaTrader runs under Wine, an MQL5 bridge exports market/account snapshots, and native Python 3.13 produces explainable technical + news-aware hints.

## Safety baseline

- Demo accounts only during development.
- No order placement code in the initial release.
- Maximum suggested risk: 0.5% of equity per trade.
- High-impact news can force a `WAIT` decision.
- Credentials and broker passwords never leave MetaTrader and never belong in Git.

## Architecture

```text
MetaTrader 5 (Wine)
  -> read-only MQL5 snapshot exporter
  -> JSON file in MQL5/Files
  -> Python 3.13 / FastAPI
  -> technical + news risk engine
  -> dashboard / human confirmation
```

## News inputs

The news layer supports configurable RSS feeds and is seeded for official sources such as the Federal Reserve, ECB, US Bureau of Labor Statistics, and US EIA. Optional licensed providers can be added later for Reuters-grade headlines and structured economic calendars.

Each hint contains:
- action: BUY, SELL, or WAIT
- technical score and reasons
- relevant headlines and affected currencies
- news-risk level
- entry zone, invalidation/stop, and risk budget
- timestamp and data freshness

## Local setup

```bash
cp .env.example .env
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn meta_trader_ai.api:app --reload
```

Open `http://127.0.0.1:8000/docs`.

## MT5 bridge

Compile `mt5/ReadOnlySnapshotBridge.mq5` in MetaEditor and attach it to one chart. It contains no trading functions. Set `MT5_SNAPSHOT_PATH` in `.env` to the generated `mt5_snapshot.json` file.

## MT5 read-only signal panel

`mt5/ReadOnlySignalPanel.mq5` displays the local API decision directly on an
MT5 chart. It contains no order-placement or order-modification functions.

1. Copy the file into MetaTrader's `MQL5/Experts` directory and compile it.
2. In MT5, open Tools > Options > Expert Advisors.
3. Enable "Allow WebRequest for listed URL" and add
   `http://127.0.0.1:8000`.
4. Keep the Python API running.
5. Attach `ReadOnlySignalPanel` to the chart.
6. Keep global Algo Trading disabled during development.

The panel displays connection status, symbol, WAIT/BUY/SELL decision,
confidence, technical score, news risk, UTC generation time, and read-only
guidance. A directional bias is never an instruction or guarantee, and manual
validation remains required.

## Export M15 history for backtesting

`mt5/HistoricalCsvExporter.mq5` is a one-shot, read-only script. It exports complete OHLCV history without placing or modifying orders.

1. Pull the latest repository changes.
2. Copy `HistoricalCsvExporter.mq5` into MetaTrader's `MQL5/Scripts` directory.
3. Open it in MetaEditor and compile it.
4. In MT5, open an `XAUUSD_o` M15 chart and load enough history.
5. Drag `HistoricalCsvExporter` from Navigator > Scripts onto the chart.
6. Keep these inputs:
   - `InputSymbol = XAUUSD_o`
   - `InputTimeframe = PERIOD_M15`
   - `InputBars = 50000` (roughly up to two trading years)
   - `IncludeCurrentBar = false`
   - `OutputFile = xauusd_m15_history.csv`
7. Find the result under `MQL5/Files/xauusd_m15_history.csv`.

The CSV columns are:

```text
time,open,high,low,close,tick_volume,spread,real_volume
```

`time` is broker-server time, `spread` is stored in points, and the currently forming candle is excluded by default to prevent look-ahead contamination. The actual number of rows depends on how much history the broker makes available in the terminal.

> This project is an educational decision-support tool, not a promise of profit or individualized financial advice.
