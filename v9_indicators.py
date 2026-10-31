
"""Saudi AI Sniper V9 - Advanced Technical Strategies.

Research only. Uses completed daily OHLCV candles.
Signals are generated at candle close, not intraday.
"""

import numpy as np
import pandas as pd


def series(frame, name):
    return pd.to_numeric(
        frame[name], errors="coerce"
    ).astype(float)


def ema(s, n):
    return s.ewm(
        span=n, adjust=False, min_periods=n
    ).mean()


def rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(
        alpha=1/n, adjust=False,
        min_periods=n
    ).mean()
    avg_down = down.ewm(
        alpha=1/n, adjust=False,
        min_periods=n
    ).mean()
    ratio = avg_up / avg_down.replace(0, np.nan)
    result = 100 - 100 / (1 + ratio)
    result = result.mask(
        (avg_down == 0) & (avg_up > 0), 100
    )
    return result.mask(
        (avg_up == 0) & (avg_down == 0), 50
    )


def atr(high, low, close, n=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(
        alpha=1/n, adjust=False,
        min_periods=n
    ).mean()


def advanced_features(frame):
    x = frame.copy()

    o = series(x, "Open")
    h = series(x, "High")
    l = series(x, "Low")
    c = series(x, "Close")
    v = series(x, "Volume").clip(lower=0)

    for n in (9, 20, 50, 100, 200):
        x[f"ema{n}"] = ema(c, n)

    x["rsi14"] = rsi(c)
    x["atr14"] = atr(h, l, c)
    x["atr_pct"] = x["atr14"] / c * 100

    # MACD
    x["macd_v9"] = ema(c, 12) - ema(c, 26)
    x["macd_signal_v9"] = ema(
        x["macd_v9"], 9
    )
    x["macd_hist"] = (
        x["macd_v9"] - x["macd_signal_v9"]
    )

    # Bollinger Bands
    middle = c.rolling(20).mean()
    std = c.rolling(20).std()
    x["bb_mid"] = middle
    x["bb_upper"] = middle + 2 * std
    x["bb_lower"] = middle - 2 * std
    x["bb_width"] = (
        (x["bb_upper"] - x["bb_lower"])
        / middle.replace(0, np.nan)
    )

    # Keltner Channels
    x["kc_upper"] = x["ema20"] + 1.5*x["atr14"]
    x["kc_lower"] = x["ema20"] - 1.5*x["atr14"]

    # Stochastic oscillator
    lowest = l.rolling(14).min()
    highest = h.rolling(14).max()
    x["stoch_k"] = (
        100 * (c-lowest)
        / (highest-lowest).replace(0, np.nan)
    ).rolling(3).mean()
    x["stoch_d"] = x["stoch_k"].rolling(3).mean()

    # Stochastic RSI
    rmin = x["rsi14"].rolling(14).min()
    rmax = x["rsi14"].rolling(14).max()
    x["stoch_rsi"] = (
        100*(x["rsi14"]-rmin)
        / (rmax-rmin).replace(0, np.nan)
    )

    # CCI
    typical = (h+l+c)/3
    tp_ma = typical.rolling(20).mean()
    mean_dev = typical.rolling(20).apply(
        lambda a: np.mean(
            np.abs(a-np.mean(a))
        ),
        raw=True
    )
    x["cci"] = (
        (typical-tp_ma)
        / (0.015*mean_dev.replace(0, np.nan))
    )

    # Money Flow Index
    money = typical*v
    positive = money.where(
        typical > typical.shift(), 0
    )
    negative = money.where(
        typical < typical.shift(), 0
    )
    ratio = (
        positive.rolling(14).sum()
        / negative.rolling(14).sum().replace(
            0, np.nan
        )
    )
    x["mfi"] = 100-100/(1+ratio)

    # Chaikin Money Flow
    multiplier = (
        (2*c-h-l)
        / (h-l).replace(0, np.nan)
    ).fillna(0)
    x["cmf"] = (
        (multiplier*v).rolling(20).sum()
        / v.rolling(20).sum().replace(
            0, np.nan
        )
    )

    # On Balance Volume
    direction = np.sign(c.diff()).fillna(0)
    x["obv"] = (direction*v).cumsum()
    x["obv_ema20"] = ema(x["obv"], 20)

    # ADX
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0),
        0
    )
    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0),
        0
    )
    atr_value = x["atr14"].replace(
        0, np.nan
    )
    plus_di = (
        100*plus_dm.ewm(
            alpha=1/14, adjust=False,
            min_periods=14
        ).mean()/atr_value
    )
    minus_di = (
        100*minus_dm.ewm(
            alpha=1/14, adjust=False,
            min_periods=14
        ).mean()/atr_value
    )
    dx = (
        100*(plus_di-minus_di).abs()
        / (plus_di+minus_di).replace(
            0, np.nan
        )
    )
    x["adx"] = dx.ewm(
        alpha=1/14, adjust=False,
        min_periods=14
    ).mean()
    x["plus_di"] = plus_di
    x["minus_di"] = minus_di

    # Donchian: previous candles only
    x["donchian_high20"] = (
        h.shift(1).rolling(20).max()
    )
    x["donchian_low20"] = (
        l.shift(1).rolling(20).min()
    )
    x["donchian_high55"] = (
        h.shift(1).rolling(55).max()
    )

    # Previous-day pivot points
    x["pivot"] = (
        h.shift(1)+l.shift(1)+c.shift(1)
    )/3
    x["pivot_r1"] = (
        2*x["pivot"]-l.shift(1)
    )
    x["pivot_s1"] = (
        2*x["pivot"]-h.shift(1)
    )

    # Ichimoku - no future data used
    conversion = (
        h.rolling(9).max()
        + l.rolling(9).min()
    )/2
    base = (
        h.rolling(26).max()
        + l.rolling(26).min()
    )/2
    x["ichimoku_conversion"] = conversion
    x["ichimoku_base"] = base
    x["cloud_a"] = (
        (conversion+base)/2
    ).shift(26)
    x["cloud_b"] = (
        (
            h.rolling(52).max()
            + l.rolling(52).min()
        )/2
    ).shift(26)

    # Volume and liquidity
    x["rel_volume_v9"] = (
        v/v.shift(1).rolling(20).mean()
        .replace(0, np.nan)
    )

    if "turnover" in x.columns:
        traded_value = pd.to_numeric(
            x["turnover"], errors="coerce"
        )
    else:
        traded_value = c*v

    x["avg_value_v9"] = (
        traded_value.rolling(20).mean()
    )

    # Rolling volume-weighted average price
    # Not intraday VWAP
    x["rolling_vwap20"] = (
        (typical*v).rolling(20).sum()
        / v.rolling(20).sum().replace(
            0, np.nan
        )
    )

    # Historical volatility
    x["volatility20"] = (
        c.pct_change().rolling(20).std()
        * np.sqrt(252)*100
    )

    x["bullish_candle_v9"] = c > o
    x["close_position_v9"] = (
        (c-l)/(h-l).replace(0, np.nan)
    )

    return x


def candidate_signals(frame):
    """Return independent historical signal series."""

    x = advanced_features(frame)
    c = series(x, "Close")
    l = series(x, "Low")

    liquid = x["avg_value_v9"] >= 20_000_000
    volume = x["rel_volume_v9"] >= 1.2

    trend = (
        (c > x["ema50"])
        & (x["ema20"] > x["ema50"])
    )
    long_trend = (
        trend & (c > x["ema200"])
    )

    momentum = (
        (x["macd_hist"] > 0)
        & (x["macd_hist"] >
           x["macd_hist"].shift(1))
    )

    strong = (
        (x["adx"] >= 20)
        & (x["plus_di"] > x["minus_di"])
    )

    bullish = x["bullish_candle_v9"]

    signals = {
        "v9_donchian_breakout": (
            trend & volume
            & (c > x["donchian_high20"])
            & (x["rsi14"] < 80)
        ),

        "v9_55day_breakout": (
            long_trend & volume
            & (c > x["donchian_high55"])
            & strong
        ),

        "v9_adx_trend": (
            trend & strong & momentum
            & (x["adx"] >
               x["adx"].shift(1))
        ),

        "v9_ema_pullback": (
            long_trend
            & (l <= x["ema20"]*1.01)
            & (c > x["ema20"])
            & bullish
            & (x["rsi14"].between(40, 68))
        ),

        "v9_bollinger_rebound": (
            (l <= x["bb_lower"])
            & (c > x["bb_lower"])
            & bullish
            & (x["rsi14"] < 45)
        ),

        "v9_squeeze_breakout": (
            (
                x["bb_upper"].shift(1)
                < x["kc_upper"].shift(1)
            )
            & (
                x["bb_lower"].shift(1)
                > x["kc_lower"].shift(1)
            )
            & (c > x["bb_upper"])
            & volume
        ),

        "v9_macd_reversal": (
            (x["macd_hist"] > 0)
            & (x["macd_hist"].shift(1) <= 0)
            & (x["rsi14"] > 40)
            & (c > x["ema50"])
        ),

        "v9_stochastic_reversal": (
            (x["stoch_k"] > x["stoch_d"])
            & (
                x["stoch_k"].shift(1)
                <= x["stoch_d"].shift(1)
            )
            & (x["stoch_k"] < 45)
            & bullish
        ),

        "v9_money_flow": (
            trend
            & (x["mfi"] > 55)
            & (x["cmf"] > 0.05)
            & (x["obv"] > x["obv_ema20"])
        ),

        "v9_ichimoku_trend": (
            (c > x[["cloud_a", "cloud_b"]]
             .max(axis=1))
            & (x["ichimoku_conversion"]
               > x["ichimoku_base"])
            & strong
        ),

        "v9_vwap_reclaim": (
            (c > x["rolling_vwap20"])
            & (
                c.shift(1)
                <= x["rolling_vwap20"].shift(1)
            )
            & volume
            & momentum
        ),

        "v9_cci_reversal": (
            (x["cci"] > -100)
            & (x["cci"].shift(1) <= -100)
            & bullish
            & (x["rsi14"] < 55)
        ),

        "v9_pivot_reclaim": (
            (c > x["pivot"])
            & (l <= x["pivot"])
            & bullish
            & trend
        ),

        "v9_stoch_rsi_recovery": (
            (x["stoch_rsi"] > 20)
            & (
                x["stoch_rsi"].shift(1)
                <= 20
            )
            & trend
            & bullish
        ),

        "v9_volume_accumulation": (
            (x["cmf"] > 0.1)
            & (x["mfi"] > 50)
            & volume
            & (c > x["ema20"])
        ),
    }

    return {
        name: (
            signal & liquid
        ).fillna(False).astype(bool)
        for name, signal in signals.items()
    }
