
"""Saudi AI Sniper - Fundamental Engine V1 (research only).

Reads SAHMK Pro company, ratios and financial statements.
Does not place orders, modify the scanner, or send alerts.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://api.sahmk.sa/api/v1"
TIMEOUT = 20


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def find_value(data, aliases, depth=0):
    """Find a reported field without guessing missing financial values."""
    if depth > 5:
        return None
    if isinstance(data, dict):
        for key in aliases:
            if key in data:
                value = number(data[key])
                if value is not None:
                    return value
        for value in data.values():
            if isinstance(value, (dict, list)):
                result = find_value(value, aliases, depth + 1)
                if result is not None:
                    return result
    elif isinstance(data, list):
        for item in data:
            result = find_value(item, aliases, depth + 1)
            if result is not None:
                return result
    return None


def fetch(session, path, params=None):
    try:
        response = session.get(
            BASE_URL + path,
            params=params,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, (dict, list)):
            return None, "Unexpected response format"
        return data, None
    except requests.RequestException as exc:
        return None, f"HTTP request failed: {type(exc).__name__}"
    except ValueError:
        return None, "Invalid JSON response"


def safe_div(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def metric(data, *names):
    return find_value(data, names)


def analyze(symbol, session=None):
    symbol = str(symbol).strip().upper().replace(".SR", "")
    if not symbol.isdigit() or len(symbol) > 6:
        raise ValueError("Use a valid Saudi stock symbol, e.g. 1120")

    key = os.environ.get("SAHMK_API_KEY", "").strip()
    if session is None:
        if not key:
            raise RuntimeError("Missing SAHMK_API_KEY environment variable")
        session = requests.Session()
        session.headers.update({"X-API-Key": key})

    company, company_error = fetch(session, f"/company/{symbol}/")
    ratios, ratios_error = fetch(session, f"/analytics/ratios/{symbol}/")
    annual, annual_error = fetch(
        session,
        f"/financials/{symbol}/",
        {"period": "annual", "history": "5y", "metrics": "extended", "limit": 5},
    )
    if company is None and ratios is None and annual is None:
        raise RuntimeError("No SAHMK financial data available; check API access")

    company = company or {}
    ratios = ratios or {}
    annual = annual or {}

    # Do not infer financial statement units or missing values.
    price = metric(company, "price", "last_price", "last", "current_price")
    book_value = metric(
        ratios, "book_value_per_share", "book_value", "bvps"
    )
    if book_value is None:
        book_value = metric(company, "book_value_per_share", "book_value", "bvps")
    pe = metric(ratios, "pe_ratio", "price_to_earnings", "pe", "trailing_pe")
    if pe is None:
        pe = metric(company, "pe_ratio", "pe", "trailing_pe")
    pb = metric(ratios, "pb_ratio", "price_to_book", "p_b", "pb")
    if pb is None:
        pb = metric(company, "pb_ratio", "price_to_book", "pb")
    if pb is None:
        pb = safe_div(price, book_value)

    roe = metric(ratios, "roe", "return_on_equity", "roe_percent")
    roa = metric(ratios, "roa", "return_on_assets", "roa_percent")

    balance = annual.get("balance_sheets", []) if isinstance(annual, dict) else []
    income = annual.get("income_statements", []) if isinstance(annual, dict) else []
    cashflows = annual.get("cash_flows", []) if isinstance(annual, dict) else []

    def latest(records):
        if not isinstance(records, list) or not records:
            return {}
        valid = [x for x in records if isinstance(x, dict)]
        return max(valid, key=lambda x: str(x.get("report_date", ""))) if valid else {}

    bs = latest(balance)
    inc = latest(income)
    cf = latest(cashflows)

    assets = metric(bs, "total_assets")
    liabilities = metric(bs, "total_liabilities")
    equity = metric(bs, "stockholders_equity", "total_equity")
    debt = metric(bs, "total_debt")
    revenue = metric(inc, "total_revenue", "revenue")
    net_income = metric(inc, "net_income")
    operating_cf = metric(cf, "operating_cash_flow")
    free_cf = metric(cf, "free_cash_flow")
    debt_to_equity = safe_div(debt, equity)
    net_margin = safe_div(net_income, revenue)
    if net_margin is not None:
        net_margin *= 100

    # Descriptive flags, not a trained probability or a fair-value estimate.
    flags = []
    if pb is not None and 0 < pb < 1:
        flags.append("Price below reported book value; investigate asset quality")
    if net_income is not None and net_income <= 0:
        flags.append("Non-positive reported net income")
    if operating_cf is not None and operating_cf < 0:
        flags.append("Negative operating cash flow")
    if debt_to_equity is not None and debt_to_equity > 1.5:
        flags.append("High debt relative to equity; sector context required")
    if equity is not None and equity <= 0:
        flags.append("Non-positive equity")

    # Banking and insurance balance sheets are not directly comparable to
    # industrial debt/equity and cash-flow thresholds.
    sector = (
        company.get("sector_name") or company.get("sector_name_ar")
        if isinstance(company, dict) else None
    )

    return {
        "symbol": symbol,
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "company_name": (
            company.get("name") or company.get("name_ar")
            or company.get("name_en") if isinstance(company, dict) else None
        ),
        "sector": sector,
        "metrics": {
            "last_price_sar": price,
            "book_value_per_share_sar": book_value,
            "price_to_book": pb,
            "price_to_earnings": pe,
            "return_on_equity_reported": roe,
            "return_on_assets_reported": roa,
            "total_assets_reported": assets,
            "total_liabilities_reported": liabilities,
            "shareholders_equity_reported": equity,
            "total_debt_reported": debt,
            "debt_to_equity": debt_to_equity,
            "annual_revenue_reported": revenue,
            "annual_net_income_reported": net_income,
            "net_profit_margin_percent": net_margin,
            "operating_cash_flow_reported": operating_cf,
            "free_cash_flow_reported": free_cf,
        },
        "report_dates": {
            "balance_sheet": bs.get("report_date"),
            "income_statement": inc.get("report_date"),
            "cash_flow": cf.get("report_date"),
        },
        "risk_flags": flags,
        "data_errors": {
            k: v for k, v in {
                "company": company_error,
                "ratios": ratios_error,
                "financials": annual_error,
            }.items() if v
        },
        "assessment": "Research only; no investment signal or success probability",
        "notes": [
            "Reported ratios may use different periods and percentage units.",
            "Do not compare financial ratios across unrelated sectors.",
            "Book value alone does not establish fair value.",
            "For historical backtests, use the original publication dates to avoid look-ahead bias.",
        ],
    }


def main():
    symbols = sys.argv[1:] or ["1120"]
    output = {}
    for symbol in symbols:
        try:
            output[symbol] = analyze(symbol)
            print(f"OK: {symbol}")
        except Exception as exc:
            output[symbol] = {"error": str(exc)}
            print(f"FAILED: {symbol}: {exc}")
    path = Path("fundamental_results.json")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
