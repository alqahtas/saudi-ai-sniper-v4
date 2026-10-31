
"""Saudi AI Sniper V9 - Full Saudi Market Research Lab.

Tests all TASI stocks with 15 technical strategies.

Research only:
- No automatic trading
- No Telegram alerts
- No changes to live bot
"""

import json
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from market_scanner import (
    sahmk_session,
    tasi_equities,
)

from sniper_core import daily, clean

from ai_strategy_engine import (
    complete_daily,
    build_features,
    backtest,
    train_eligible,
    strategy_rank,
)

from v9_indicators import (
    candidate_signals,
)

TRAIN_BARS = 252
TEST_BARS = 63
STEP_BARS = 63
WARMUP_BARS = 120

MIN_HISTORY = (
    WARMUP_BARS
    + TRAIN_BARS
    + TEST_BARS
)

MIN_FOLDS = 2
MIN_SELECTED_FOLDS = 2
MIN_TEST_TRADES = 15

OUTPUT_JSON = "v8_market_lab_results.json"
OUTPUT_CSV = "v8_market_lab_summary.csv"


def get_symbols():
    session = sahmk_session()
    items = tasi_equities(session)

    symbols = []
    seen = set()

    for item in items:
        if isinstance(item, str):
            value = item

        elif isinstance(item, dict):
            value = (
                item.get("symbol")
                or item.get("ticker")
                or item.get("code")
                or item.get("stock_code")
            )

        else:
            continue

        if value is None:
            continue

        symbol = (
            str(value)
            .strip()
            .upper()
            .replace(".SR", "")
        )

        if (
            symbol.isdigit()
            and symbol not in seen
        ):
            seen.add(symbol)
            symbols.append(symbol)

    return symbols


def walk_forward(frame, signals):
    folds = []

    train_start = WARMUP_BARS

    while (
        train_start
        + TRAIN_BARS
        + TEST_BARS
        <= len(frame)
    ):
        train_end = (
            train_start
            + TRAIN_BARS
            - 1
        )

        test_start = train_end + 1

        test_end = (
            test_start
            + TEST_BARS
            - 1
        )

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

            training_stats = eligible[
                selected
            ]

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
        fold
        for fold in folds
        if (
            fold["selected_strategy"]
            is not None
            and fold["test"] is not None
        )
    ]

    traded = [
        fold
        for fold in selected
        if fold["test"]["trades"] > 0
    ]

    trades = sum(
        fold["test"]["trades"]
        for fold in traded
    )

    result = {
        "folds": len(folds),
        "selected_folds": len(selected),
        "traded_folds": len(traded),
        "test_trades": trades,
        "win_rate_pct": None,
        "average_net_pct": None,
        "compounded_net_pct": None,
        "worst_fold_drawdown_pct": None,
        "sufficient_folds": False,
        "status": "insufficient_evidence",
    }

    if trades == 0:
        return result

    wins = sum(
        fold["test"]["trades"]
        * fold["test"]["win_rate_pct"]
        / 100
        for fold in traded
    )

    net_sum = sum(
        fold["test"]["trades"]
        * fold["test"]["avg_net_pct"]
        for fold in traded
    )

    compound = 1.0
    drawdowns = []

    for fold in traded:
        stats = fold["test"]

        total = stats.get(
            "total_net_pct"
        )

        if total is not None:
            compound *= (
                1 + total / 100
            )

        dd = stats.get(
            "max_drawdown_pct"
        )

        if dd is not None:
            drawdowns.append(dd)

    win_rate = (
        100 * wins / trades
    )

    average_net = (
        net_sum / trades
    )

    compounded = (
        100 * (compound - 1)
    )

    result.update({
        "win_rate_pct": round(
            win_rate, 2
        ),

        "average_net_pct": round(
            average_net, 3
        ),

        "compounded_net_pct": round(
            compounded, 3
        ),

        "worst_fold_drawdown_pct": (
            round(min(drawdowns), 3)
            if drawdowns else None
        ),
    })

    sufficient = (
        len(folds) >= MIN_FOLDS
        and len(selected)
        >= MIN_SELECTED_FOLDS
        and trades >= MIN_TEST_TRADES
    )

    result["sufficient_folds"] = (
        sufficient
    )

    if not sufficient:
        result["status"] = (
            "insufficient_evidence"
        )

    elif (
        average_net > 0
        and compounded > 0
    ):
        result["status"] = (
            "historically_positive"
        )

    else:
        result["status"] = (
            "historically_negative"
        )

    return result


def analyze(symbol):
    raw = daily(symbol)

    if raw is None:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
            "bars": 0,
        }

    raw = complete_daily(
        clean(raw)
    )

    if (
        raw is None
        or len(raw) < MIN_HISTORY
    ):
        return {
            "symbol": symbol,
            "status": "insufficient_history",
            "bars": (
                0 if raw is None
                else len(raw)
            ),
        }

    frame = build_features(raw)

    signals = candidate_signals(
        frame
    )

    folds = walk_forward(
        frame,
        signals,
    )

    summary = summarize(
        folds
    )

    active = [
        name
        for name, signal in signals.items()
        if bool(signal.iloc[-1])
    ]

    return {
        "symbol": symbol,
        "status": "ok",
        "bars": len(frame),
        "summary": summary,
        "folds": folds,
        "active_strategies": active,
    }


def main():
    symbols = get_symbols()

    if not symbols:
        raise RuntimeError(
            "No TASI stocks returned"
        )

    print(
        "SAUDI AI SNIPER V9",
        "FULL MARKET RESEARCH",
        flush=True,
    )

    print(
        "TOTAL STOCKS:",
        len(symbols),
        flush=True,
    )

    report = {
        "engine": (
            "Saudi AI Sniper V9"
        ),

        "generated_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),

        "universe_size": len(
            symbols
        ),

        "settings": {
            "train_bars": TRAIN_BARS,
            "test_bars": TEST_BARS,
            "step_bars": STEP_BARS,
            "minimum_folds": MIN_FOLDS,
            "minimum_selected_folds":
                MIN_SELECTED_FOLDS,
            "minimum_test_trades":
                MIN_TEST_TRADES,
            "strategy_source":
                "v9_indicators.py",
        },

        "stocks": {},
    }

    rows = []

    for number, symbol in enumerate(
        symbols,
        start=1,
    ):
        print(
            f"[{number}/{len(symbols)}]",
            symbol,
            flush=True,
        )

        try:
            result = analyze(
                symbol
            )

        except Exception as exc:
            result = {
                "symbol": symbol,
                "status": "error",
                "error": str(exc),
                "traceback":
                    traceback.format_exc(
                        limit=3
                    ),
            }

        report["stocks"][symbol] = (
            result
        )

        summary = (
            result.get("summary")
            or {}
        )

        rows.append({
            "symbol": symbol,

            "status":
                result["status"],

            "bars":
                result.get("bars"),

            "folds":
                summary.get("folds"),

            "selected_folds":
                summary.get(
                    "selected_folds"
                ),

            "traded_folds":
                summary.get(
                    "traded_folds"
                ),

            "test_trades":
                summary.get(
                    "test_trades"
                ),

            "win_rate_pct":
                summary.get(
                    "win_rate_pct"
                ),

            "average_net_pct":
                summary.get(
                    "average_net_pct"
                ),

            "compounded_net_pct":
                summary.get(
                    "compounded_net_pct"
                ),

            "research_status":
                summary.get(
                    "status"
                ),

            "sufficient_folds":
                summary.get(
                    "sufficient_folds"
                ),

            "active_strategies":
                ",".join(
                    result.get(
                        "active_strategies",
                        []
                    )
                ),

            "error":
                result.get("error"),
        })

        print(
            "STATUS:",
            result["status"],

            "RESEARCH:",
            summary.get(
                "status", "-"
            ),

            "TRADES:",
            summary.get(
                "test_trades", 0
            ),

            flush=True,
        )

    Path(
        OUTPUT_JSON
    ).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    pd.DataFrame(
        rows
    ).to_csv(
        OUTPUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    counts = Counter(
        result["status"]
        for result
        in report["stocks"].values()
    )

    research_counts = Counter(
        (
            result.get("summary")
            or {}
        ).get("status")
        for result
        in report["stocks"].values()
        if result["status"] == "ok"
    )

    print(
        "\n===== V9 RESULTS =====",
        flush=True,
    )

    print(
        "TOTAL:",
        len(symbols)
    )

    print(
        "ANALYZED:",
        counts["ok"]
    )

    print(
        "INSUFFICIENT HISTORY:",
        counts["insufficient_history"]
    )

    print(
        "ERRORS:",
        counts["error"]
    )

    print(
        "HISTORICALLY POSITIVE:",
        research_counts[
            "historically_positive"
        ]
    )

    print(
        "HISTORICALLY NEGATIVE:",
        research_counts[
            "historically_negative"
        ]
    )

    print(
        "INSUFFICIENT EVIDENCE:",
        research_counts[
            "insufficient_evidence"
        ]
    )

    if counts["ok"] == 0:
        raise RuntimeError(
            "No successful analyses"
        )

    print(
        "V9 RESEARCH COMPLETED",
        flush=True,
    )


if __name__ == "__main__":
    main()
