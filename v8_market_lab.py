
"""Saudi AI Sniper V8 - Full Market Walk-Forward Lab.

Research only. No orders or Telegram alerts.
Tests all TASI equities returned by SAHMK.
"""

import json
import math
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from market_scanner import sahmk_session, tasi_equities
from sniper_core import daily, clean
from ai_strategy_engine import (
    build_features,
    candidate_signals,
    backtest,
    train_eligible,
    strategy_rank,
    complete_daily,
)

TRAIN_BARS = 252
TEST_BARS = 63
STEP_BARS = 63
WARMUP_BARS = 120
MIN_FOLDS = 2

OUTPUT_JSON = "v8_market_lab_results.json"
OUTPUT_CSV = "v8_market_lab_summary.csv"


def get_symbols():
    session = sahmk_session()
    universe = tasi_equities(session)

    symbols = []
    seen = set()

    for item in universe:
        if isinstance(item, str):
            symbol = item
        elif isinstance(item, dict):
            symbol = (
                item.get("symbol")
                or item.get("ticker")
                or item.get("code")
                or item.get("stock_code")
            )
        else:
            continue

        if symbol is None:
            continue

        symbol = str(symbol).replace(".SR", "").strip()

        if symbol.isdigit() and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)

    return symbols


def safe_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def walk_forward(frame, signals):
    folds = []
    train_start = WARMUP_BARS

    while (
        train_start + TRAIN_BARS + TEST_BARS
        <= len(frame)
    ):
        train_end = train_start + TRAIN_BARS - 1
        test_start = train_end + 1
        test_end = test_start + TEST_BARS - 1

        eligible = {}

        for name, signal in signals.items():
            stats = backtest(
                frame, signal, train_start, train_end
            )

            if train_eligible(stats):
                eligible[name] = stats

        selected = (
            max(
                eligible,
                key=lambda name: strategy_rank(
                    eligible[name]
                ),
            )
            if eligible else None
        )

        test = (
            backtest(
                frame,
                signals[selected],
                test_start,
                test_end,
            )
            if selected else None
        )

        folds.append({
            "train_start": str(
                frame.index[train_start]
            )[:10],
            "train_end": str(
                frame.index[train_end]
            )[:10],
            "test_start": str(
                frame.index[test_start]
            )[:10],
            "test_end": str(
                frame.index[test_end]
            )[:10],
            "selected_strategy": selected,
            "train": (
                eligible.get(selected)
                if selected else None
            ),
            "test": test,
        })

        train_start += STEP_BARS

    return folds


def summarize(folds):
    selected = [
        fold for fold in folds
        if fold["selected_strategy"]
        and fold["test"] is not None
    ]

    trades = sum(
        fold["test"]["trades"]
        for fold in selected
    )

    winning_trades = sum(
        fold["test"]["trades"]
        * fold["test"]["win_rate_pct"] / 100
        for fold in selected
        if fold["test"]["win_rate_pct"] is not None
    )

    weighted_return = sum(
        fold["test"]["trades"]
        * fold["test"]["avg_net_pct"]
        for fold in selected
        if fold["test"]["avg_net_pct"] is not None
    )

    compound = 1.0
    for fold in selected:
        value = fold["test"]["total_net_pct"]
        if value is not None:
            compound *= 1 + value / 100

    return {
        "folds": len(folds),
        "selected_folds": len(selected),
        "test_trades": trades,
        "win_rate_pct": (
            round(100 * winning_trades / trades, 2)
            if trades else None
        ),
        "average_net_pct": (
            round(weighted_return / trades, 3)
            if trades else None
        ),
        "compounded_net_pct": (
            round(100 * (compound - 1), 3)
            if trades else None
        ),
        "sufficient_folds": (
            len(selected) >= MIN_FOLDS
        ),
    }


def analyze(symbol):
    raw = daily(symbol)

    if raw is None:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
        }

    raw = complete_daily(clean(raw))

    if raw is None or len(raw) < 450:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
            "bars": 0 if raw is None else len(raw),
        }

    frame = build_features(raw)
    signals = candidate_signals(frame)
    folds = walk_forward(frame, signals)

    summary = summarize(folds)

    return {
        "symbol": symbol,
        "status": "ok",
        "bars": len(frame),
        "summary": summary,
        "folds": folds,
        "active_strategies": [
            name for name, signal in signals.items()
            if bool(signal.iloc[-1])
        ],
    }


def main():
    symbols = get_symbols()

    if not symbols:
        raise RuntimeError("No TASI symbols returned")

    print(
        "V8 FULL MARKET WALK-FORWARD",
        "Stocks:", len(symbols),
        flush=True,
    )

    report = {
        "engine": "Saudi AI Sniper V8 Market Lab",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "universe_size": len(symbols),
        "settings": {
            "train_bars": TRAIN_BARS,
            "test_bars": TEST_BARS,
            "step_bars": STEP_BARS,
            "minimum_folds": MIN_FOLDS,
        },
        "stocks": {},
    }

    rows = []

    for number, symbol in enumerate(symbols, 1):
        print(
            f"[{number}/{len(symbols)}] {symbol}",
            flush=True,
        )

        try:
            result = analyze(symbol)
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

        summary = result.get("summary") or {}

        rows.append({
            "symbol": symbol,
            "status": result["status"],
            "bars": result.get("bars"),
            "folds": summary.get("folds"),
            "selected_folds": summary.get(
                "selected_folds"
            ),
            "test_trades": summary.get(
                "test_trades"
            ),
            "win_rate_pct": summary.get(
                "win_rate_pct"
            ),
            "average_net_pct": summary.get(
                "average_net_pct"
            ),
            "compounded_net_pct": summary.get(
                "compounded_net_pct"
            ),
            "sufficient_folds": summary.get(
                "sufficient_folds"
            ),
            "error": result.get("error"),
        })

    Path(OUTPUT_JSON).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    pd.DataFrame(rows).to_csv(
        OUTPUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    successful = sum(
        result["status"] == "ok"
        for result in report["stocks"].values()
    )

    print(
        f"Analyzed: {successful}/{len(symbols)}",
        flush=True,
    )
    print("Saved:", OUTPUT_JSON, OUTPUT_CSV)

    if successful == 0:
        raise RuntimeError(
            "No successful analyses"
        )


if __name__ == "__main__":
    main()
