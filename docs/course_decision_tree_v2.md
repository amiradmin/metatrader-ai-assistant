# Course-informed Decision Tree v2

This branch experiments with translating selected rules from the uploaded trading course into causal, testable M15 decision gates. It does **not** replace the existing news, spread, daily-risk, confidence, or H1/H4 safety controls.

## Source-derived rules implemented

The course prioritizes **trend -> level -> slope/power**. The first implementation therefore treats market context before entry setup.

Implemented rules:

- Strong trend / spike priority: do not preserve a directional hint that trades directly against a strong trend or spike.
- Range behavior: BUY is only eligible near the lower edge, SELL only near the upper edge, and the middle of the range is a no-entry area.
- FTB/STB observability: lower/upper edge touch counts are exported as diagnostics so first/second-return behavior can be measured before it is allowed to change direction or confidence.
- Range breakout validation: the breakout candle must satisfy all three course conditions before the move is tagged `VALID_UP`/`VALID_DOWN`:
  1. breakout body >= `1.5 x` the recent step-body average;
  2. more than 50% of the candle range is beyond the range boundary;
  3. price breaks the wick-defined range extreme and closes outside it.
- Failed candidates are tagged `FAKE_UP` / `FAKE_DOWN` and can veto a same-direction entry.

## Engineering proxies that still require validation

The PDF gives qualitative market-structure rules but does not provide machine-ready thresholds for every concept. The following values are implementation hypotheses, not claims copied from the course:

- strong/weak trend thresholds based on efficiency ratio and displacement in ATR;
- spike body >= 1.8 ATR;
- range edge = outer 20% of the detected range;
- range lookback = 24 completed M15 candles;
- touch tolerance = 0.20 ATR;
- recent step-body window = 5 candles.

These are configurable and must be measured in historical + walk-forward tests before production weighting.

## Safety property

`apply_course_decision_tree()` can only:

- keep an existing BUY/SELL hint; or
- downgrade it to `WAIT`.

It never converts `WAIT` into BUY/SELL and never reverses direction. Existing high-impact-news, spread, account-risk, strict confidence, and H1/H4 confirmation gates remain downstream.

## New `/hint` diagnostics

The response now exposes:

- `course_tree_status`: `CONFIRM`, `BLOCK`, `OBSERVE`, or `DISABLED`
- `course_regime`
- `course_range_zone`
- `course_breakout_status`
- `course_lower_touch_count`
- `course_upper_touch_count`

## Configuration

```env
COURSE_DECISION_TREE_ENABLED=true
COURSE_RANGE_LOOKBACK=24
COURSE_TREND_LOOKBACK=20
COURSE_RANGE_EDGE_FRACTION=0.20
COURSE_TOUCH_TOLERANCE_ATR=0.20
COURSE_BREAKOUT_STEP_BARS=5
COURSE_BREAKOUT_BODY_MULTIPLE=1.50
```

## Next validation steps

1. Run unit tests.
2. Backtest `main` vs this branch on the same XAUUSD M15 history.
3. Compare expectancy, PF, drawdown, trade count, and the exact trades blocked by the course tree.
4. Run walk-forward validation.
5. Only after evidence improves, add FVG / supply-demand / QM / MTR / ACD and multi-target exit experiments.
