"""Saudi AI Sniper V5 — experimental research/backtest module.

No broker integration, no live pricing, no guaranteed probabilities.
Uses historical daily OHLCV data; same-bar stop/target ambiguity is treated as a stop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd

STRATEGIES = ("support_rebound", "breakout", "crash_hunter")


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    for col in d:
        d[col] = pd.to_numeric(d[col], errors='coerce')
    d = d.dropna().sort_index()
    c, h, l, v = (d[x] for x in ('Close','High','Low','Volume'))
    d['ema20'] = c.ewm(span=20, adjust=False).mean()
    d['ema50'] = c.ewm(span=50, adjust=False).mean()
    d['ema200'] = c.ewm(span=200, adjust=False).mean()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    d['rsi'] = 100 - 100/(1 + up/down.replace(0,np.nan))
    d['rsi'] = d['rsi'].fillna(100)
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    d['macd'] = e12-e26
    d['macd_signal'] = d['macd'].ewm(span=9, adjust=False).mean()
    prev = c.shift(1)
    tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    d['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    # Shift support/resistance to avoid including current candle in its own breakout threshold.
    d['support20'] = l.shift(1).rolling(20).min()
    d['support60'] = l.shift(1).rolling(60).min()
    d['resistance20'] = h.shift(1).rolling(20).max()
    d['resistance60'] = h.shift(1).rolling(60).max()
    d['volume20'] = v.shift(1).rolling(20).mean()
    d['turnover20'] = (c*v).shift(1).rolling(20).mean()
    d['rvol'] = v/d['volume20'].replace(0,np.nan)
    d['return20'] = c/c.shift(20)-1
    d['close_pos'] = (c-l)/(h-l).replace(0,np.nan)
    d['lower_wick'] = (d[['Open','Close']].min(axis=1)-l)/(h-l).replace(0,np.nan)
    return d


def signals(d: pd.DataFrame) -> pd.DataFrame:
    out = d.copy()
    liquid = d['turnover20'] >= 20_000_000
    rise = d['Close'] > d['Open']
    bullish = rise & (d['close_pos'] >= 0.65)
    out['support_rebound'] = (liquid & (d['Low'] <= 1.012*d['support20']) &
        (d['Close'] > d['support20']) & bullish & (d['lower_wick'] >= 0.2) &
        (d['ema20'] >= .97*d['ema50']) & (d['rvol'] >= 1.1))
    out['breakout'] = (liquid & (d['Close'] > 1.005*d['resistance20']) &
        (d['rvol'] >= 1.5) & bullish & (d['ema20'] > d['ema50']))
    out['crash_hunter'] = (liquid & (d['return20'] <= -0.12) &
        (d['Low'] <= 1.02*d['support60']) & (d['Close'] > d['support60']) &
        bullish & (d['rsi'] < 45))
    return out


@dataclass
class Outcome:
    signal_date: str
    outcome: str
    return_pct: float
    bars: int


def evaluate(d: pd.DataFrame, signal_index: int, holding_bars: int = 15,
             risk_atr: float = 1.5, reward_r: float = 2.0,
             trading_cost_pct: float = 0.003) -> Optional[Outcome]:
    """Enter at next day's open. Conservative when stop & target touch in same bar."""
    if signal_index + holding_bars >= len(d):
        return None
    row = d.iloc[signal_index]
    atr = float(row['atr'])
    if not (np.isfinite(atr) and atr > 0):
        return None
    entry = float(d['Open'].iloc[signal_index+1])
    if entry <= 0:
        return None
    risk = risk_atr * atr
    stop, target = entry-risk, entry+reward_r*risk
    if stop <= 0:
        return None
    outcome, price, bars = 'timeout', float(d['Close'].iloc[signal_index+holding_bars]), holding_bars
    for j in range(signal_index+1,signal_index+holding_bars+1):
        r = d.iloc[j]
        lo, hi, op = float(r['Low']), float(r['High']), float(r['Open'])
        if op <= stop:
            outcome, price, bars = 'stop_gap', op, j-signal_index
            break
        if op >= target:
            outcome, price, bars = 'target_gap', op, j-signal_index
            break
        if lo <= stop:  # choose loss if both hit within candle
            outcome, price, bars = 'stop', stop, j-signal_index
            break
        if hi >= target:
            outcome, price, bars = 'target', target, j-signal_index
            break
    return Outcome(str(d.index[signal_index].date()), outcome,
                   round(100*((price/entry)-1-trading_cost_pct),2), bars)


def historical_stats(d: pd.DataFrame, strategy: str, lookback_days: int=1260,
                     holding_bars: int=15) -> dict:
    """Non-overlapping forward tests. Not an out-of-sample validation guarantee."""
    if strategy not in STRATEGIES:
        raise ValueError('Unknown strategy')
    start = max(210,len(d)-lookback_days)
    outcomes=[]
    last_entry=-999
    for i in range(start, len(d)-holding_bars):
        if not bool(d[strategy].iloc[i]) or i-last_entry <= holding_bars:
            continue
        res=evaluate(d,i,holding_bars=holding_bars)
        if res is not None:
            outcomes.append(res)
            last_entry=i
    if not outcomes:
        return {'samples':0,'win_rate_pct':None,'mean_return_pct':None,
                'worst_return_pct':None,'target_hit_rate_pct':None,'outcomes':[]}
    returns = [x.return_pct for x in outcomes]
    return {'samples':len(outcomes),
            'win_rate_pct':round(100*sum(x>0 for x in returns)/len(returns),1),
            'mean_return_pct':round(float(np.mean(returns)),2),
            'worst_return_pct':round(float(np.min(returns)),2),
            'target_hit_rate_pct':round(100*sum(x.outcome in ('target','target_gap') for x in outcomes)/len(outcomes),1),
            'outcomes':[x.__dict__ for x in outcomes]}


def analyze(ticker: str, data: pd.DataFrame) -> dict:
    d=signals(indicators(data))
    if len(d)<230:
        return {'ticker':ticker,'error':'At least 230 daily candles required for EMA200 warm-up'}
    last=d.iloc[-1]
    stats={name:historical_stats(d,name) for name in STRATEGIES}
    current=[name for name in STRATEGIES if bool(last[name])]
    liquid=bool(last['turnover20'] >= 20_000_000)
    trend=bool(last['Close']>last['ema50'] and last['ema20']>last['ema50'])
    vol=bool(last['rvol'] >= 1.1)
    momentum=bool(last['macd']>last['macd_signal'])
    levels=bool(last['Close']>last['support20'] and last['Close']<last['resistance60']*1.05)
    # This is an explainable signal-quality score, NOT a predicted win probability.
    score=sum([25*liquid,20*trend,15*vol,15*momentum,10*levels,15*bool(current)])
    # Candidate LIMIT levels use the historical close as reference, not a live quote.
    # ATR stop and targets are indicative and must be checked against exchange ticks.
    last_close = float(last['Close'])
    atr = float(last['atr'])
    support = float(last['support20'])
    resistance = float(last['resistance60'])
    entry = stop = target1 = target2 = None
    rr1 = None
    if current and np.isfinite(atr) and atr > 0 and np.isfinite(support):
        candidate = min(last_close * 0.995, max(support * 1.005, last_close - 0.25 * atr))
        risk = max(1.5 * atr, candidate - support + 0.25 * atr)
        if 0 < candidate < last_close and 0 < candidate-risk:
            entry = round(candidate, 3)
            stop = round(candidate-risk, 3)
            # Targets must exceed entry and have useful R:R; avoid made-up price forecasts.
            target1 = round(candidate + 2*risk, 3)
            target2 = round(candidate + 3*risk, 3)
            rr1 = 2.0
    # No historical win-rate is used to inflate score when sample sizes are small.
    return {'ticker':ticker,'date':str(d.index[-1].date()),'close':round(float(last['Close']),3),
       'support20':round(float(last['support20']),3),'support60':round(float(last['support60']),3),
       'resistance20':round(float(last['resistance20']),3),
       'resistance60':round(float(last['resistance60']),3),
       'rsi14':round(float(last['rsi']),1),'relative_volume':round(float(last['rvol']),2),
       'avg_turnover_m_sar':round(float(last['turnover20'])/1e6,1),
       'atr14':round(float(last['atr']),3), 'quality_score_100':int(score),
       'active_strategies':current,'backtests':stats,
       'entry_reference':entry,'stop_reference':stop,
       'target1_reference':target1,'target2_reference':target2,
       'reward_risk_target1':rr1,
       'notes':'Historical daily-price research only; no real-time order flow or verified probability.'}


def download_daily(ticker: str, years: int=5) -> pd.DataFrame:
    import yfinance as yf
    return yf.download(f'{ticker}.SR',period=f'{years}y',interval='1d',
        multi_level_index=False,auto_adjust=False,progress=False,threads=False,timeout=20)


if __name__=='__main__':
    import json,sys
    for ticker in sys.argv[1:] or ['2010']:
        try:
            print(json.dumps(analyze(ticker,download_daily(ticker)),ensure_ascii=False,indent=2))
        except Exception as e:
            print(json.dumps({'ticker':ticker,'error':str(e)},ensure_ascii=False))
