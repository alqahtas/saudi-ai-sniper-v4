"""Saudi AI Sniper V5 daily analysis bridge.

Historical candles only; no live quote, no automated trading.
Weekly scans are handled separately in market_scanner.weekly_setup().
"""
import math
from sniper_v5 import analyze, download_daily

WATCHLIST = ('2222 1120 1180 1150 1010 1050 2010 2020 1211 7010 '
             '7020 7030 2082 2083 5110 4001 4002 4003 4004 4005 '
             '4006 4013 4164 4200 4300 7200 7202 7203 7204 8010').split()
CAPITAL_SAR = 50_000
MAX_ENTRY_SAR = 10_000
RISK_PER_TRADE_SAR = 150
STOP_LOSS_PCT = .015  # retained for legacy imports; ATR stop governs below
MIN_AVG_VALUE_SAR = 20_000_000
MIN_REL_VOLUME = 1.10
MIN_RR = 1.5
SUPPORT_WINDOW = 30


def daily(ticker):
    """Download daily candles; never silently treat them as live prices."""
    try:
        data = download_daily(ticker, years=5)
        if data is None or len(data) < 230:
            return None
        return data
    except Exception as exc:
        print(f"Daily history unavailable for {ticker}: {type(exc).__name__}: {exc}", flush=True)
        return None


def _finite_positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def calculate(ticker, candles):
    """Generate a conditional daily setup from *historical* analysis."""
    result = analyze(ticker, candles)
    if not isinstance(result, dict) or result.get('error'):
        return None

    price = _finite_positive(result.get('close'))
    atr = _finite_positive(result.get('atr14'))
    support = _finite_positive(result.get('support20'))
    if price is None or atr is None or support is None:
        return None

    entry = round(price + 0.1 * atr, 3)
    stop = round(min(support - 0.15 * atr, entry - 1.5 * atr), 3)
    risk = entry - stop
    if stop <= 0 or risk <= 0:
        return None

    rr = max(2.0, MIN_RR)
    target = round(entry + rr * risk, 3)
    second = round(entry + 3 * risk, 3)
    active = [str(name).strip() for name in (result.get('active_strategies') or []) if str(name).strip()]
    backtests = result.get('backtests') or {}
    tested = {key: value for key, value in backtests.items()
              if key in active and isinstance(value, dict)}
    enough = any(
        (value.get('samples') or 0) >= 20
        and value.get('mean_return_pct') is not None
        and float(value['mean_return_pct']) > 0
        for value in tested.values()
    )
    liquidity = float(result.get('avg_turnover_m_sar') or 0)
    score = float(result.get('quality_score_100') or 0)
    relative_volume = float(result.get('relative_volume') or 0)
    liquid = math.isfinite(liquidity) and liquidity >= MIN_AVG_VALUE_SAR / 1_000_000

    shares_by_budget = int(MAX_ENTRY_SAR // entry)
    shares_by_risk = int(RISK_PER_TRADE_SAR // risk)
    shares = max(0, min(shares_by_budget, shares_by_risk))
    strong = bool(active) and enough and liquid and score >= 75 and shares > 0

    return {
        'Ø§ÙØ³ÙÙ': str(ticker), 'Ø§ÙØªØ§Ø±ÙØ®': result.get('date'),
        'Ø§ÙØ¥Ø·Ø§Ø±_Ø§ÙØ²ÙÙÙ': 'daily', 'ÙÙØ¹_Ø§ÙØ¨ÙØ§ÙØ§Øª': 'Ø´ÙÙØ¹ ÙÙÙÙØ© ØªØ§Ø±ÙØ®ÙØ© - ÙÙØ³Øª Ø£Ø³Ø¹Ø§Ø±ÙØ§ ÙØ­Ø¸ÙØ©',
        'Ø§ÙØ¥ØºÙØ§Ù_Ø§ÙÙØ±Ø¬Ø¹Ù': price, 'Ø§ÙØ¯Ø¹Ù': support,
        'Ø§ÙÙÙØ§ÙÙØ©': result.get('resistance60'),
        'Ø¨Ø¯Ø§ÙØ©_ÙØ·Ø§Ù_Ø§ÙØ´Ø±Ø§Ø¡': entry, 'ÙÙØ§ÙØ©_ÙØ·Ø§Ù_Ø§ÙØ´Ø±Ø§Ø¡': entry,
        'ÙÙÙ_ØªÙØ¯ÙØ±Ù': stop, 'ÙØ¯Ù_ØªÙØ¯ÙØ±Ù': target, 'ÙØ¯Ù_Ø«Ø§Ù': second,
        'ÙÙÙØ©_Ø§ÙØªØ¯Ø§ÙÙ_Ø§ÙÙÙÙÙØ©_Ø§ÙÙØªÙØ³Ø·Ø©_ÙÙÙÙÙ': liquidity,
        'Ø§ÙØ­Ø¬Ù_Ø§ÙÙØ³Ø¨Ù': relative_volume, 'RSI14': result.get('rsi14'),
        'EMA20': None, 'EMA50': None, 'ÙØ³Ø¨Ø©_Ø§ÙØ¹Ø§Ø¦Ø¯_ÙÙÙØ®Ø§Ø·Ø±Ø©': rr,
        'ÙÙÙØ©_Ø§ÙØªØ±Ø§Ø¶ÙØ©': shares, 'Ø¥Ø¬ÙØ§ÙÙ_Ø§ÙØ´Ø±Ø§Ø¡_Ø±ÙØ§Ù': round(shares * entry, 2),
        'Ø§ÙØ­Ø§ÙØ©': 'ÙØ±ØµØ© ÙØ´Ø±ÙØ·Ø©' if strong else 'ÙØ§ Ø¯Ø®ÙÙ',
        'Ø¯Ø±Ø¬Ø©_Ø§ÙØ¬ÙØ¯Ø©': score, 'Ø§ÙØ§Ø³ØªØ±Ø§ØªÙØ¬ÙØ§Øª': ','.join(active),
        'Ø§Ø®ØªØ¨Ø§Ø±Ø§Øª_ØªØ§Ø±ÙØ®ÙØ©': {
            key: {field: value.get(field) for field in ('samples', 'win_rate_pct', 'mean_return_pct')}
            for key, value in tested.items()
        },
        'Ø§ÙØ³Ø¨Ø¨_Ø¹ÙØ¯_Ø§ÙØ±ÙØ¶': '' if strong else 'Ø´Ø±ÙØ· Ø§ÙØ§Ø³ØªØ±Ø§ØªÙØ¬ÙØ© Ø£Ù Ø§Ø®ØªØ¨Ø§Ø±ÙØ§ Ø£Ù Ø§ÙØ³ÙÙÙØ© Ø£Ù ÙØ³ØªÙÙ Ø§ÙÙØ®Ø§Ø·Ø±Ø© ØºÙØ± ÙØ§ÙÙØ©',
    }
