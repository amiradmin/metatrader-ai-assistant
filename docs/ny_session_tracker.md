# New York Session Tracker

`MetaTraderAI` can observe the New York trading session without changing the strict execution strategy.

## Default window

- 08:00 to 17:00 US Eastern Time (ET)
- US daylight-saving time is calculated automatically (second Sunday in March through first Sunday in November).
- The tracker is analytics-only and never places, modifies, or closes orders.

## What it records

For each unique directional M15 candidate during the New York window it records:

- action and confidence
- technical score
- Strict75 result: opened, waiting pullback, or rejected
- high-level rejection reason (confidence, news, spread, risk, max-open, other gate)
- Shadow72/Shadow70 status
- broker spread

The event log is written to `MQL5/Files/ny_session_tracker.csv`.

## Panel

A separate panel on the right side of the chart shows:

- PRE-OPEN / OPEN / CLOSED / WEEKEND
- current Eastern Time and configured NY window
- directional candidates
- Strict entries and pullback waits
- rejection counts
- Shadow C72/C70 eligible/opened counts
- Strict closed-trade P/L during the NY session
- latest candidate decision

## Important limitation

Candidate counters start when the EA is attached/restarted. Strict closed-trade P/L is reconstructed from MT5 history for the current NY session. The tracker is diagnostic and should not be used by itself to loosen live entry filters.
