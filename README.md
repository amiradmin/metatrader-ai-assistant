# MetaTrader AI Assistant

A read-only-first MetaTrader 5 assistant for Ubuntu. MetaTrader runs under Wine, MQL5 bridges export market/account snapshots plus higher-timeframe context, and native Python 3.13 produces explainable M15-first technical + news-aware hints.

## Safety baseline

- Demo accounts only during development.
- No order placement code in the initial release.
- Maximum suggested risk: 0.5% of equity per trade.
- High-impact news can force a `WAIT` decision.
- Credentials and broker passwords never leave MetaTrader and never belong in Git.
- TipRanks is confirmation context only; it cannot create a trade direction by itself.
- H1/H4 market structure is observational until backtest and forward-test evidence justifies weighting it.

## Architecture

```text
MetaTrader 5 (Wine)
  -> read-only MQL5 M15 snapshot exporter
  -> read-only H1/H4 market-context exporter
  -> JSON files in MQL5/Files
  -> Python 3.13 / FastAPI
  -> EMA9/EMA21 + RSI14 + ATR14 + momentum + spread
  -> confirmed-swing HH/HL/LH/LL + BOS/CHOCH observer
  -> news-risk gate
  -> optional TipRanks higher-timeframe confirmation (max +/-6 confidence)
  -> dashboard / human confirmation
```

## M15-first signal engine

The primary decision timeframe is M15. The bridge exports completed candles only so indicators do not depend on the still-forming candle.

The baseline score combines:
- EMA 9 / EMA 21 trend
- RSI 14
- ATR 14
- four-bar momentum
- current spread relative to ATR
- news risk

Confidence is dynamic. A directional hint must still meet the configured safety threshold before it can remain `BUY` or `SELL`; otherwise it is converted to `WAIT`.

## H1/H4 market-structure observer

`mt5/ReadOnlyMarketContextBridge.mq5` exports completed H1 and H4 OHLC arrays. Python then identifies confirmed fractal swing highs/lows and derives:

- bullish `HH_HL` structure
- bearish `LH_LL` structure
- mixed/range structure
- `BOS_UP` / `BOS_DOWN`
- `CHOCH_UP` / `CHOCH_DOWN`
- H1/H4 agreement with the current M15 directional bias

Swing points require candles on both sides, so the detector uses confirmed structure rather than repainting the newest bar. BOS/CHOCH is emitted only when the latest completed close crosses a confirmed swing level after the previous completed close had not crossed it.

The observer currently contributes **zero confidence points**. Its status is exposed as `CONFIRM`, `OPPOSE`, `MIXED`, or `OBSERVE` so we can measure whether it improves expectancy before allowing it to affect live/demo decisions.

Configure the local higher-timeframe JSON path in `.env`:

```bash
MT5_CONTEXT_PATH=/home/amir/.mt5/drive_c/users/amir/AppData/Roaming/MetaQuotes/Terminal/REPLACE_WITH_TERMINAL_ID/MQL5/Files/mt5_context.json
MAX_CONTEXT_AGE_SECONDS=90
MARKET_STRUCTURE_ENABLED=true
```

Attach `ReadOnlyMarketContextBridge` to one MT5 chart with the same symbol used by `ReadOnlySnapshotBridge`. The signal panel will then show H1 trend/structure/event, H4 trend/structure/event, and the MTF observer status.

## News inputs

The news layer supports configurable RSS feeds and is seeded for official sources such as the Federal Reserve, US Bureau of Labor Statistics, and US EIA. Optional licensed providers can be added later for Reuters-grade headlines and structured economic calendars.

Each hint contains:
- action: BUY, SELL, or WAIT
- technical score and reasons
- H1/H4 market structure and MTF observer status
- relevant headlines and affected currencies
- news-risk level
- confidence and risk budget
- timestamp and data freshness

## TipRanks context

The TipRanks ChatGPT connector is not a Python package installed inside this repository. The local application therefore treats TipRanks as optional external context rather than pretending to call the ChatGPT connector directly.

Fresh context can be submitted to the local API:

```bash
curl -X PUT http://127.0.0.1:8000/context/tipranks \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol": "EURUSD",
    "price": 1.16079,
    "change_percentage": 0.14,
    "price_avg_50": 1.14951,
    "price_avg_200": 1.16308,
    "updated_at": "2026-09-03T10:30:00Z",
    "source": "TipRanks"
  }'
```

The payload is stored under `data/tipranks_context.json`, which is ignored by Git. Context is used only when it matches the current MT5 symbol and is newer than `TIPRANKS_CONTEXT_MAX_AGE_MINUTES`.

TipRanks contributes at most +/-6 confidence points. It never overrides the high-impact-news gate and never turns an M15 `WAIT` into a trade direction.

## Local setup

```bash
cp .env.example .env
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn meta_trader_ai.api:app --reload
```

If ROS 2 pytest plugins leak into the environment, run the project tests with plugin autoload disabled:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q
```

Open `http://127.0.0.1:8000/docs`.

## MT5 bridge

Compile `mt5/ReadOnlySnapshotBridge.mq5` in MetaEditor and attach it to one chart. It contains no trading functions. Its default timeframe is `PERIOD_M15` and it exports completed OHLC candles plus the live bid/ask spread. Set `MT5_SNAPSHOT_PATH` in `.env` to the generated `mt5_snapshot.json` file.

For higher-timeframe structure, also compile and attach `mt5/ReadOnlyMarketContextBridge.mq5`. It exports 100 completed H1 and H4 candles by default to `mt5_context.json`. Set `MT5_CONTEXT_PATH` in `.env` to that generated file.

## MT5 read-only signal panel

`mt5/ReadOnlySignalPanel.mq5` displays the local API decision directly on an MT5 chart. It contains no order-placement or order-modification functions.

1. Copy the file into MetaTrader's `MQL5/Experts` directory and compile it.
2. In MT5, open Tools > Options > Expert Advisors.
3. Enable "Allow WebRequest for listed URL" and add `http://127.0.0.1:8000`.
4. Keep the Python API running.
5. Attach `ReadOnlySignalPanel` to the chart.
6. Attach `ReadOnlyMarketContextBridge` so H1/H4 structure is available.
7. Keep global Algo Trading disabled during development.

The panel displays connection status, symbol, WAIT/BUY/SELL decision, confidence, technical score, H1/H4 market structure, MTF observer status, news risk, TipRanks context, UTC generation time, and read-only guidance. A directional bias is never an instruction or guarantee, and manual validation remains required.

## Ubuntu desktop signal alerts

`desktop-signal-notifier` polls the local `/hint` API and shows an Ubuntu desktop notification only when the already-guarded API response is `BUY` or `SELL` and `risk_guard_status` is `OK`. It never connects to a broker and never places an order. Alerts are deduplicated to one per symbol and completed M15 candle.

Start the FastAPI service first, then run this in a second terminal:

```bash
uv run desktop-signal-notifier
```

It checks every 15 seconds by default. For a one-time connection check:

```bash
uv run desktop-signal-notifier --once
```

Ubuntu normally provides `notify-send` through `libnotify-bin`. If it is not installed, the notifier prints the exact package name instead of failing silently.

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
