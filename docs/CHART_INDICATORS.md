# Chart indicator evidence: EMA25/75/140 and Fibonacci

This change adds the user's chart settings to analytical outputs. It does not
change the selected Futures execution profile, open positions, orders, leverage,
stop loss, take profit, fees, cooldown, or existing risk/reward gates.

- EMA periods: 25, 75, 140. Recursive first-close seed (TradingView/pandas
  adjust=False), at least 140 completed samples. Shorter histories explicitly
  return insufficient_ema_history, never a fabricated EMA140.
- Displayed retracement: 0, .236, .382, .786, 1; 0 is the range low and 1 the high.
- Extension: 0, .236, .382, .786, 1, 1.272 from THREE alternating confirmed pivots,
  C + (B-A)*ratio. It is not the older two-point 138.2/150/161.8 projection.
- Both sides of every pivot require two completed neighbors; an outside candle
  cannot imply an arbitrary pivot order. Broken ranges are invalidated.
- Crosses of .236/.382 toward .786 are labelled analytical rebound evidence.
  A crossing alone does not create an executable trade or guarantee a target.
- No pivot/invalid range produces null levels rather than invented anchors.
- All new outputs are JSON serializable. Colors describe the chart only and
  have no effect on calculations.

Existing EMA retest profile confluence/target rules are retained for existing
contracts and regression compatibility. The new chart evidence is stored under
chart_indicators; do not confuse it with legacy fib_ratio/fib_extensions.

## Integration
Daily research output, per-timeframe summaries, intraday setup records and
hourly/15m EMA alert events carry chart_indicators. EMA alerts include compact
chart levels, clearly separate from legacy projections. Daily research inherits
the existing historical data adapter; the hourly/15m route filters completed
cash-session candles before computing indicators. Saudi signals remain long-only.

## Validation and rollout

Unit tests use deterministic candles and injected/mocked clients. No live
account is contacted. A pull request and passing CI are code validation only;
this change has not been merged or deployed. Forward testing is still required
before changing live strategy gates or making profitability claims.
