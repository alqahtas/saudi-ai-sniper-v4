
"""Saudi AI Sniper V8.1 - Full Market Walk-Forward Lab.

Research only. No orders or Telegram alerts.

- Scans every TASI equity returned by SAHMK
- Uses training-only strategy selection
- Tests on non-overlapping future windows
- Counts actual out-of-sample trades
- Does not approve empty or tiny samples
- Reports insufficient evidence separately
"""

import json
import math
import traceback
from collections import Counter
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

MIN_HISTORY = (
    WARMUP_BARS + TRAIN_BARS + TEST_BARS
)

MIN_FOLDS = 2
MIN_SELECTED_FOLDS = 2
MIN_TEST_TRADES = 15

OUTPUT_JSON = "v8_market_lab_results.json"
OUTPUT_CSV = "v8_market_lab_summary.csv"


def symbol_of(item):
    if isinstance(item, str):
        value = item
    elif isinstance(item, dict):
        value = next(
            (
                item.get(key)
                for key in (
                    "symbol",
                    "ticker",
                    "code",
                    "stock_code",
                )
                if item.get(key) is not None
            ),
            None,
        )
    else:
        return None

    if value is None:
        return None

    symbol = str(value).strip().upper()
    symbol = symbol.replace(".SR", "")

    return symbol if symbol.isdigit() else None


def get_symbols():
    session = sahmk_session()
    items = tasi_equities(session)

    symbols = []
    seen = set()

    for item in items:
        symbol = symbol_of(item)

        if symbol and symbol not in seen:
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
                frame,
                signal,
                train_start,
                train_end,
            )

            if train_eligible(stats):
                eligible[name] = stats

        selected = None
        training_stats = None
        test_stats = None

        if eligible:
            selected = max(
                eligible,
                key=lambda name: strategy_rank(
                    eligible[name]
                ),
            )

            training_stats = eligible[selected]

            test_stats = backtest(
                frame,
                signals[selected],
                test_start,
                test_end,
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
            "train": training_stats,
            "test": test_stats,
        })

        train_start += STEP_BARS

    return folds


def summarize(folds):
    selected = [
        fold for fold in folds
        if fold["selected_strategy"] is not None
        and fold["test"] is not None
    ]

    traded = [
        fold for fold in selected
        if fold["test"]["trades"] > 0
    ]

    total_trades = sum(
        fold["test"]["trades"]
        for fold in traded
    )

    result = {
        "folds": len(folds),
        "selected_folds": len(selected),
        "traded_folds": len(traded),
        "test_trades": total_trades,
        "win_rate_pct": None,
        "average_net_pct": None,
        "compounded_net_pct": None,
        "profit_factor": None,
        "worst_fold_drawdown_pct": None,
        "sufficient_folds": False,
        "status": "insufficient_evidence",
    }

    if total_trades == 0:
        return result

    winning = sum(
        fold["test"]["trades"]
        * fold["test"]["win_rate_pct"] / 100
        for fold in traded
    )

    weighted_return = sum(
        fold["test"]["trades"]
        * fold["test"]["avg_net_pct"]
        for fold in traded
    )

    gross_profit = 0.0
    gross_loss = 0.0
    compound = 1.0
    drawdowns = []

    for fold in traded:
        stats = fold["test"]
        count = stats["trades"]

        average = stats["avg_net_pct"] / 100
        win_rate = stats["win_rate_pct"] / 100
        pf = stats["profit_factor"]

        # Profit factor is aggregated from the
        # reported statistics, so this is an
        # approximation rather than trade-level PF.
        if pf is not None and pf > 0:
            net_sum = average * count

            if pf > 1:
                loss = net_sum / (pf - 1)
                if loss >= 0:
                    gross_loss += loss
                    gross_profit += loss * pf

            elif pf < 1:
                loss = net_sum / (pf - 1)
                if loss >= 0:
                    gross_loss += loss
                    gross_profit += loss * pf

        total_pct = stats["total_net_pct"]

        if total_pct is not None:
            compound *= 1 + total_pct / 100

        dd = stats["max_drawdown_pct"]

        if dd is not None:
            drawdowns.append(dd)

    win_rate = 100 * winning / total_trades
    average_net = weighted_return / total_trades
    compounded = 100 * (compound - 1)

    result.update({
        "win_rate_pct": round(win_rate, 2),
        "average_net_pct": round(
            average_net, 3
        ),
        "compounded_net_pct": round(
            compounded, 3
        ),
        "profit_factor": (
            round(gross_profit / gross_loss, 3)
            if gross_loss > 0 else None
        ),
        "worst_fold_drawdown_pct": (
            round(min(drawdowns), 3)
            if drawdowns else None
        ),
    })

    sufficient = (
        len(folds) >= MIN_FOLDS
        and len(selected) >= MIN_SELECTED_FOLDS
        and total_trades >= MIN_TEST_TRADES
    )

    result["sufficient_folds"] = sufficient

    if not sufficient:
        result["status"] = "insufficient_evidence"
    elif (
        average_net > 0
        and compounded > 0
        and (
            result["profit_factor"] is not None
            and result["profit_factor"] > 1
        )
    ):
        result["status"] = "historically_positive"
    else:
        result["status"] = "historically_negative"

    return result


def analyze(symbol):
    raw = daily(symbol)

    if raw is None:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
            "bars": 0,
        }

    raw = complete_daily(clean(raw))

    if raw is None or len(raw) < MIN_HISTORY:
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
            name
            for name, signal in signals.items()
            if bool(signal.iloc[-1])
        ],
    }


def main():
    symbols = get_symbols()

    if not symbols:
        raise RuntimeError(
            "SAHMK returned no TASI equities"
        )

    print(
        "V8.1 FULL MARKET WALK-FORWARD",
        "STOCKS:", len(symbols),
        flush=True,
    )

    report = {
        "engine": "Saudi AI Sniper V8.1 Market Lab",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "universe_size": len(symbols),
        "settings": {
            "train_bars": TRAIN_BARS,
            "test_bars": TEST_BARS,
            "step_bars": STEP_BARS,
            "minimum_folds": MIN_FOLDS,
            "minimum_selected_folds":
                MIN_SELECTED_FOLDS,
            "minimum_test_trades":
                MIN_TEST_TRADES,
        },
        "stocks": {},
    }

    rows = []

    for number, symbol in enumerate(
        symbols, start=1
    ):
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
            "traded_folds": summary.get(
                "traded_folds"
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
            "profit_factor": summary.get(
                "profit_factor"
            ),
            "research_status": summary.get(
                "status"
            ),
            "sufficient_folds": summary.get(
                "sufficient_folds"
            ),
            "error": result.get("error"),
        })

        print(
            "STATUS:",
            result["status"],
            "RESEARCH:",
            summary.get("status", "-"),
            "TRADES:",
            summary.get("test_trades", 0),
            flush=True,
        )

    Path("v8_market_lab_results.json").write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    pd.DataFrame(rows).to_csv(
        "v8_market_lab_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    counts = Counter(
        result["status"]
        for result in report["stocks"].values()
    )

    research_counts = Counter(
        (result.get("summary") or {}).get(
            "status"
        )
        for result in report["stocks"].values()
        if result["status"] == "ok"
    )

    print("\n===== FINAL RESULTS =====")
    print("TOTAL:", len(symbols))
    print("ANALYZED:", counts["ok"])
    print(
        "INSUFFICIENT HISTORY:",
        counts["insufficient_history"],
    )
    print("ERRORS:", counts["error"])
    print(
        "HISTORICALLY POSITIVE:",
        research_counts["historically_positive"],
    )
    print(
        "HISTORICALLY NEGATIVE:",
        research_counts["historically_negative"],
    )
    print(
        "INSUFFICIENT EVIDENCE:",
        research_counts["insufficient_evidence"],
    )

    if counts["ok"] == 0:
        raise RuntimeError(
            "No successful stock analyses"
        )

    print("V8.1 MARKET LAB COMPLETED")


if __name__ == "__main__":
    main()
