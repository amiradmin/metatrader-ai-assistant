# Live Shadow Mode

`MetaTraderAI.mq5` can compare the strict demo strategy against two paper-only confidence thresholds while the market is live.

Defaults:

- Strict execution: confidence >= 75. This is the only path allowed to place MT5 orders.
- Shadow A: confidence >= 72, paper only.
- Shadow B: confidence >= 70, paper only.

Both shadow strategies reuse the strict EA's spread gate, ATR/swing SL and TP construction, risk percentage, daily risk budget, and anti-chase/pullback rules. Only the confidence threshold changes, so the comparison isolates whether the strict confidence filter is excluding useful trades.

The panel shows today's strict realized P/L and two paper lines with entries, wins/losses, realized paper P/L, and `OPEN` when a paper position is active.

Closed paper trades are appended to `MQL5/Files/shadow_trade_journal.csv` with threshold, confidence, broker timestamps, side, entry type, entry/SL/TP/exit, volume, planned risk, P/L, and outcome.

Shadow mode does not call `CTrade.Buy`, `CTrade.Sell`, or any other order-placement function. It is diagnostic only.

Shadow statistics are in-memory for the current EA session/day. The CSV journal persists closed paper trades across restarts for later analysis.
