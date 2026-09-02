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

> This project is an educational decision-support tool, not a promise of profit or individualized financial advice.
