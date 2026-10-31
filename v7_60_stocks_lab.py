
"""Saudi AI Sniper V7.1 - 60 Stocks Research Lab.

Research only. Does not send Telegram alerts or orders.

Features:
- Select up to 60 TASI equities by recent traded value
- Five strategies from the existing V7 engine
- Rolling walk-forward training and testing
- Optional TASI trend filter
- CSV and JSON reports
"""

import json
import math
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from market_scanner import (
    sahmk_session,
    tasi_equities,
    select_active_equities,
)

from sniper_core import daily, clean

from ai_strategy_engine import (
    build_features,
    candidate_signals,
    backtest,
    train_eligible,
    test_eligible,
    strategy_rank,
    complete_daily,
)

LIMIT = 60
MIN_HISTORY = 420
MIN_VALUE_SAR = 20_000_000

TRAIN_BARS = 252
TEST_BARS = 63
STEP_BARS = 63
MIN_FOLDS = 2

RESULT_JSON = "v7_60_stocks_results.json"
RESULT_CSV = "v7_60_stocks_results.csv"
SUMMARY_CSV = "v7_60_stocks_summary.csv"

# Optional TASI benchmark symbols.
TASI_SYMBOLS = ["^TASI.SR", "TASI", "^TASI"]


def safe_float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def symbol_of(item):
    if isinstance(item, str):
        return item.strip().replace(".SR", "")

    if isinstance(item, dict):
        for key in (
            "symbol",
            "ticker",
            "code",
            "stock_code",
        ):
            value = item.get(key)
            if value:
                return str(value).strip().replace(
                    ".SR", ""
                )

    return None


def get_universe():
    """Use the repository's existing SAHMK universe.

    Selection is based on market activity, never on
    strategy profitability.
    """

    session = sahmk_session()
    equities = tasi_equities(session)

    # Existing scanner handles its own active/liquid ranking.
    try:
        selected = select_active_equities(
            session, equities, LIMIT
        )
    except TypeError:
        try:
            selected = select_active_equities(
                session, equities
            )
        except TypeError:
            selected = select_active_equities(
                equities
            )

    symbols = []
    seen = set()

    for item in selected:
        symbol = symbol_of(item)

        if not symbol:
            continue

        if not symbol.isdigit():
            continue

        if symbol in seen:
            continue

        seen.add(symbol)
        symbols.append(symbol)

        if len(symbols) >= LIMIT:
            break

    return symbols


def get_tasi():
    """Best-effort benchmark fetch.

    If unavailable, report missing benchmark instead
    of inventing TASI data.
    """

    for symbol in TASI_SYMBOLS:
        try:
            frame = clean(daily(symbol))

            if frame is not None and len(frame) >= 250:
                return complete_daily(frame), symbol

        except Exception:
            continue

    return None, None


def tasi_regime(tasi, dates):
    """Daily benchmark state, known at signal close."""

    if tasi is None:
        return None

    benchmark = tasi.copy().sort_index()

    close = benchmark["Close"]

    ema50 = close.ewm(
        span=50, adjust=False
    ).mean()

    ema200 = close.ewm(
        span=200, adjust=False
    ).mean()

    bullish = (
        (close > ema50)
        & (ema50 > ema200)
    )

    # Normalize indexes to dates for alignment.
    idx = pd.DatetimeIndex(benchmark.index)

    if idx.tz is not None:
        idx = idx.tz_localize(None)

    bullish.index = idx.normalize()

    target_idx = pd.DatetimeIndex(dates)

    if target_idx.tz is not None:
        target_idx = target_idx.tz_localize(None)

    target_idx = target_idx.normalize()

    # Forward fill only previously observed benchmark
    # dates, never backward fill future observations.
    aligned = bullish.reindex(
        target_idx,
        method="ffill",
    )

    aligned.index = dates

    return aligned.fillna(False)


def walk_forward(x, signals):
    """Rolling windows; selection uses training only."""

    output = []
    start = 120

    while (
        start + TRAIN_BARS + TEST_BARS
        < len(x)
    ):
        train_start = start
        train_end = start + TRAIN_BARS - 1

        test_start = train_end + 1
        test_end = min(
            test_start + TEST_BARS - 1,
            len(x) - 1,
        )

        trained = {}

        for name, signal in signals.items():
            stats = backtest(
                x,
                signal,
                train_start,
                train_end,
            )

            if train_eligible(stats):
                trained[name] = stats

        selected = None

        if trained:
            selected = max(
                trained,
                key=lambda name: strategy_rank(
                    trained[name]
                ),
            )

        if selected:
            test = backtest(
                x,
                signals[selected],
                test_start,
                test_end,
            )
        else:
            test = None

        output.append({
            "train_start": str(
                x.index[train_start]
            )[:10],
            "train_end": str(
                x.index[train_end]
            )[:10],
            "test_start": str(
                x.index[test_start]
            )[:10],
            "test_end": str(
                x.index[test_end]
            )[:10],
            "selected_strategy": selected,
            "test": test,
            "test_passed": (
                test_eligible(test)
                if test else False
            ),
        })

        start += STEP_BARS

    return output


def evaluate_signals(x, signals, tasi):
    """Evaluate unfiltered and benchmark-filtered runs."""

    baseline = walk_forward(x, signals)

    filtered = None

    if tasi is not None:
        market_ok = tasi_regime(
            tasi, x.index
        )

        filtered_signals = {
            name: (
                signal
                & market_ok
            ).fillna(False)
            for name, signal in signals.items()
        }

        filtered = walk_forward(
            x, filtered_signals
        )

    return baseline, filtered


def summarize_folds(folds):
    """Aggregate realized historical trade statistics.

    Total net percent is a compounded research metric,
    not a portfolio return for simultaneous stocks.
    """

    if not folds:
        return {
            "folds": 0,
            "selected_folds": 0,
            "passing_folds": 0,
            "test_trades": 0,
            "weighted_win_rate_pct": None,
            "weighted_avg_net_pct": None,
            "compounded_net_pct": None,
            "worst_fold_drawdown_pct": None,
        }

    selected = [
        f for f in folds
        if f["selected_strategy"]
        and f["test"] is not None
    ]

    passing = [
        f for f in selected
        if f["test_passed"]
    ]

    total_trades = sum(
        f["test"]["trades"]
        for f in selected
    )

    if total_trades:
        weighted_win = sum(
            f["test"]["win_rate_pct"]
            * f["test"]["trades"]
            for f in selected
            if f["test"]["win_rate_pct"] is not None
        ) / total_trades

        weighted_avg = sum(
            f["test"]["avg_net_pct"]
            * f["test"]["trades"]
            for f in selected
            if f["test"]["avg_net_pct"] is not None
        ) / total_trades

        compound = 1.0

        for fold in selected:
            pct = fold["test"]["total_net_pct"]

            if pct is not None:
                compound *= 1 + pct / 100

        compounded = (compound - 1) * 100

    else:
        weighted_win = None
        weighted_avg = None
        compounded = None

    drawdowns = [
        f["test"]["max_drawdown_pct"]
        for f in selected
        if f["test"]["max_drawdown_pct"] is not None
    ]

    return {
        "folds": len(folds),
        "selected_folds": len(selected),
        "passing_folds": len(passing),
        "test_trades": total_trades,
        "weighted_win_rate_pct": (
            round(weighted_win, 2)
            if weighted_win is not None
            else None
        ),
        "weighted_avg_net_pct": (
            round(weighted_avg, 3)
            if weighted_avg is not None
            else None
        ),
        "compounded_net_pct": (
            round(compounded, 3)
            if compounded is not None
            else None
        ),
        "worst_fold_drawdown_pct": (
            round(min(drawdowns), 3)
            if drawdowns else None
        ),
    }


def analyze_symbol(symbol, tasi):
    raw = clean(daily(symbol))
    raw = complete_daily(raw)

    if raw is None or len(raw) < MIN_HISTORY:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
            "bars": 0 if raw is None else len(raw),
        }

    x = build_features(raw)
    signals = candidate_signals(x)

    baseline, filtered = evaluate_signals(
        x, signals, tasi
    )

    baseline_summary = summarize_folds(
        baseline
    )

    filtered_summary = (
        summarize_folds(filtered)
        if filtered is not None
        else None
    )

    return {
        "symbol": symbol,
        "status": "ok",
        "bars": len(x),
        "last_close": safe_float(
            x["Close"].iloc[-1]
        ),
        "baseline": baseline_summary,
        "with_tasi_filter": filtered_summary,
        "baseline_folds": baseline,
        "tasi_folds": filtered,
        "active_strategies": [
            name
            for name, signal in signals.items()
            if bool(signal.iloc[-1])
        ],
    }


def save_reports(report):
    Path(RESULT_JSON).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    rows = []
    summary_rows = []

    for symbol, stock in report["stocks"].items():
        if stock["status"] != "ok":
            continue

        for mode, folds_key in (
            ("baseline", "baseline_folds"),
            ("tasi_filtered", "tasi_folds"),
        ):
            folds = stock.get(folds_key)

            if folds is None:
                continue

            for number, fold in enumerate(
                folds, start=1
            ):
                stats = fold.get("test") or {}

                rows.append({
                    "symbol": symbol,
                    "mode": mode,
                    "fold": number,
                    "train_start": fold["train_start"],
                    "train_end": fold["train_end"],
                    "test_start": fold["test_start"],
                    "test_end": fold["test_end"],
                    "selected_strategy": (
                        fold["selected_strategy"]
                    ),
                    "test_passed": fold["test_passed"],
                    **stats,
                })

        for mode, key in (
            ("baseline", "baseline"),
            ("tasi_filtered", "with_tasi_filter"),
        ):
            stats = stock.get(key)

            if stats is None:
                continue

            summary_rows.append({
                "symbol": symbol,
                "mode": mode,
                **stats,
            })

    pd.DataFrame(rows).to_csv(
        RESULT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    pd.DataFrame(summary_rows).to_csv(
        SUMMARY_CSV,
        index=False,
        encoding="utf-8-sig",
    )


def main():
    print("Saudi AI Sniper V7.1")
    print("Selecting up to 60 TASI equities...")

    universe = get_universe()

    if not universe:
        raise RuntimeError(
            "No equities returned by market scanner"
        )

    print(
        f"Selected {len(universe)} stocks:",
        ", ".join(universe),
    )

    tasi, tasi_symbol = get_tasi()

    print(
        "TASI benchmark:",
        tasi_symbol or "unavailable",
    )

    report = {
        "engine": "Saudi AI Sniper V7.1",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "requested_stocks": LIMIT,
        "selected_stocks": len(universe),
        "universe": universe,
        "selection_note": (
            "Universe supplied by existing SAHMK "
            "market scanner. Verify its liquidity "
            "ranking before interpreting results."
        ),
        "tasi_benchmark": tasi_symbol,
        "settings": {
            "train_bars": TRAIN_BARS,
            "test_bars": TEST_BARS,
            "step_bars": STEP_BARS,
            "target_pct": 1.5,
            "stop_pct": 1.0,
            "round_trip_cost_pct": 0.3,
        },
        "stocks": {},
    }

    for number, symbol in enumerate(
        universe, start=1
    ):
        print(
            f"[{number}/{len(universe)}] {symbol}",
            flush=True,
        )

        try:
            result = analyze_symbol(
                symbol, tasi
            )

        except Exception as exc:
            result = {
                "symbol": symbol,
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(
                    limit=3
                ),
            }

        report["stocks"][symbol] = result

        if result["status"] == "ok":
            summary = result["baseline"]

            print(
                "  trades:",
                summary["test_trades"],
                "win rate:",
                summary["weighted_win_rate_pct"],
            )
        else:
            print(
                "  status:",
                result["status"],
            )

    save_reports(report)

    successful = sum(
        stock["status"] == "ok"
        for stock in report["stocks"].values()
    )

    print(
        f"Completed: {successful}/{len(universe)}"
    )

    print(
        "Saved:",
        RESULT_JSON,
        RESULT_CSV,
        SUMMARY_CSV,
    )

    if successful == 0:
        sys.exit(
            "No successful stock analyses"
        )


if __name__ == "__main__":
    main()
