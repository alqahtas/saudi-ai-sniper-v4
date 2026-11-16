# Hourly Fibonacci / 15-minute entry, v2

Levels enabled in both drawings: 0, 0.236, 0.382, 0.786, 1.
The supplied screenshots show 0.5, 0.618 and all >1 levels disabled.

- Draw on completed H1 candles; each pivot needs two CLOSED neighbours on both sides.
- LONG/CALL: A high, B low, C high. SHORT/PUT: A low, B high, C low.
- Retracement of B→C: C - (C-B) × ratio.
- Three-point projection: C + (B-A) × ratio. This is a pullback/reversal
  drawing, not a conventional continuation-profit projection beyond C.
- Require EMA25 > EMA75 > EMA140 for LONG on H1 and M15; reverse for SHORT.
- Latest completed M15 candle must touch an enabled level and close in the
  trade direction beyond both that level and M15 EMA25. Touch alone is insufficient.
- Build levels only from H1 bars already closed before that M15 candle began.
- Stop: nearest confirmed support below LONG entry or resistance above SHORT entry.
- Targets: distinct enabled retracement/projection prices in the profit direction,
  nearest first. A nearer target that fails after estimated costs is a HOLD,
  not an excuse to skip resistance and manufacture a better risk/reward ratio.
- No percentages are success probabilities. Unit tests demonstrate mechanics,
  not a profitable edge or historical win rate. No live orders were used in tests.

## Rollout

These are source changes in a PR, not a Northflank deployment.
Positions opened under the old Futures profile keep their original ownership,
stop and target identities. New profile: FUTURES_STRATEGY_PROFILE=fib_h1_m15_v2.
The new Futures profile retains the existing 15x isolated, fee/slippage/funding
reserves, minimum 6% estimated net margin return, maximum 4% estimated margin
risk, RR≥1.5, passive GTX limits, 120-second pending expiry and signal replay guard.
It cannot silently change old positions to the new strategy.

Saudi V10's primary alerts now use saudi_fib.evaluate; LONG only. Existing
Telegram delivery, freshness, market breadth and duplicate suppression remain.
SAUDI_ESTIMATED_FEE_RATE defaults to 0.0017825 per side, an editable conservative
estimate rather than a broker-specific fee quotation; slippage reserve is 0.0002.

US service uses the same pure calculation, resolves unique US stock contracts,
requests 60d H1 / 10d M15 RTH history, rejects delayed/stale/crossed snapshots,
and saves results/latest.json (US_RESULTS_PATH overrides). No order methods exist.
US_ESTIMATED_FEE_RATE defaults to 0.001 per side with a 0.0002 slippage reserve;
these are estimates for underlying-stock analysis, not option-premium returns.
The 15:30–16:00 US partial hourly bar is excluded instead of being treated as a
full hour. A working authenticated IBKR endpoint is still required.

MT5 is a later adapter. This change neither installs an EA nor connects to MT5.

## Read-only test command

python scripts/run_offline_tests.py

All outbound socket connections are blocked for the entire suite.

## Reference conventions

TradingView: https://www.tradingview.com/support/solutions/43000518137-trend-based-fib-extension-drawing-tool/
IBKR contract discovery and snapshot fields:
https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-staging/
