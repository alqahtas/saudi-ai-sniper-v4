
"""Saudi AI Sniper — Strategy Lab V6.

Historical strategy comparison only.
Does not trade or send Telegram alerts.
"""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from market_scanner import (
    sahmk_session,
    tasi_equities,
    select_active_equities,
)
from sniper_core import (
    daily,
    clean,
    indicators,
    strategy_research,
)

LOG = logging.getLogger("strategy-lab")

OUT_JSON = Path("strategy_lab_results.json")
OUT_CSV = Path("strategy_lab_results.csv")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    session = sahmk_session()

    universe = select_active_equities(
        tasi_equities(session),
        session,
    )

    records = []
    failures = []

    for index, (symbol, name) in enumerate(
        universe.items(), 1
    ):
        try:
            candles = clean(daily(symbol))

            if candles is None:
                raise ValueError(
                    "Not enough valid daily history"
                )

            selected, approved, results, signals = (
                strategy_research(
                    indicators(candles)
                )
            )

            for strategy, detail in results.items():
                train = detail["train"]
                test = detail["test"]

                records.append({
                    "symbol": symbol,
                    "company": name,
                    "strategy": strategy,
                    "selected_on_training":
                        strategy == selected,
                    "approved":
                        bool(approved and strategy == selected),
                    "train_trades": train["trades"],
                    "train_win_rate_pct":
                        train["win_rate"],
                    "train_avg_return_pct":
                        train["avg_return"],
                    "test_trades": test["trades"],
                    "test_win_rate_pct":
                        test["win_rate"],
                    "test_avg_return_pct":
                        test["avg_return"],
                    "test_total_return_pct":
                        test["total_return"],
                    "test_max_drawdown_pct":
                        test["max_drawdown"],
                    "test_profit_factor":
                        test["profit_factor"],
                    "latest_signal":
                        bool(signals[strategy].iloc[-1]),
                })

            LOG.info(
                "LAB %d/%d %s selected=%s approved=%s",
                index,
                len(universe),
                symbol,
                selected,
                approved,
            )

        except Exception as exc:
            failures.append({
                "symbol": symbol,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            })

            LOG.warning(
                "LAB %d/%d %s failed: %s",
                index,
                len(universe),
                symbol,
                exc,
            )

    if records:
        with OUT_CSV.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(records[0]),
            )
            writer.writeheader()
            writer.writerows(records)

    payload = {
        "generated_at_utc":
            datetime.now(timezone.utc).isoformat(),
        "note": (
            "Research only. Historical results "
            "are not future guarantees."
        ),
        "stocks_selected": len(universe),
        "stocks_analyzed": len({
            row["symbol"] for row in records
        }),
        "stocks_failed": failures,
        "strategy_results": records,
    }

    OUT_JSON.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"Analyzed {payload['stocks_analyzed']}"
        f"/{len(universe)} stocks; "
        f"{len(records)} strategy results"
    )

    if not records:
        raise RuntimeError(
            "No strategy results; inspect JSON errors"
        )


if __name__ == "__main__":
    main()
