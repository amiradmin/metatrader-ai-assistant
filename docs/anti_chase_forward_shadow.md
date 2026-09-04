# Anti-Chase Forward Shadow A/B/C/D

This research branch prepares a paper-only forward comparison for XAUUSD_o/M15 without changing the current strict demo order path.

## Frozen live path

The current live/demo route remains unchanged:

- Confidence: 75
- Risk: 0.5% max
- RR: 2.0
- Daily loss guard: 1.5%
- Spread guards unchanged
- Anti-Chase live setting unchanged at MaxExtensionAtr=1.50, PullbackZoneAtr=0.35, PullbackMaxBars=4

## Forward shadow candidates

- A: CURRENT_E1.50 — Anti-Chase ON, extension 1.50, zone 0.35, wait 4
- B: E3.25_Z0.35_W4 — Anti-Chase ON, extension 3.25, zone 0.35, wait 4
- C: E3.75_Z0.35_W4 — Anti-Chase ON, extension 3.75, zone 0.35, wait 4
- D: NO_ANTI_CHASE — Anti-Chase OFF

All shadows use Confidence 75 and the same trade-plan/risk/spread/daily-loss rules as the strict route. They are paper-only and must never call CTrade, OrderSend, Buy, Sell, or any MT5 order-placement API.

## Promotion rule

Do not promote a shadow candidate from historical results alone. Collect genuinely forward data after 2026-09-04 and require at least 20 future trades or 20 completed trading days, then compare:

- trades
- win rate
- profit factor
- expectancy R/trade
- net R
- maximum drawdown
- average USD/day
- saved losses versus no-anti-chase benchmark
- missed winners versus no-anti-chase benchmark

A shadow candidate can only be considered for a manual demo change after repeated positive forward evidence. No automatic live parameter changes are allowed.

## Deployment safety

The helper include on this branch is not wired into `MetaTraderAI_Core.mqh` yet. Keep `main` and the currently deployed EA untouched during the New York session. Integration, compilation, and MT5 deployment should happen after the session window, with tests and a manual compile check first.
