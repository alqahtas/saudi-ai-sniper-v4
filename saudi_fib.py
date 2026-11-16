"""Saudi cash-session adapter for hourly Fibonacci / 15m alerts."""
from dataclasses import dataclass
from decimal import Decimal as D

import pandas as pd

from fib_h1_m15 import Setup, select_setup
from saudi_ema_stack import _session_candles, MARKET_OPEN, MARKET_CLOSE, RIYADH


@dataclass(frozen=True)
class CashSetup:
    plan: Setup
    confirmed_at: pd.Timestamp
    estimated_net_reward: D
    estimated_net_risk: D


def evaluate(hourly_payload, fifteen_payload, quote_price, quote_stamp, now, *,
             fee_rate=D(".0017825"), slippage=D(".0002")):
    now = pd.Timestamp(now).tz_convert(RIYADH)
    if now.dayofweek not in (6,0,1,2,3) or not MARKET_OPEN <= now.time() < MARKET_CLOSE:
        return None
    hour, lower = (_session_candles(payload, n, now)
                   for payload,n in ((hourly_payload,60),(fifteen_payload,15)))
    if hour.empty or lower.empty:
        return None
    for frame, minutes in ((hour,60),(lower,15)):
        opened = now.normalize()+pd.Timedelta(hours=10)
        expected = pd.date_range(opened,now.floor(f"{minutes}min"),freq=f"{minutes}min",inclusive="left")
        if not frame.index[frame.index>=opened].tz_convert(RIYADH).equals(expected):
            return None
    def rows(frame, minutes):
        return [dict(open_time=int(stamp.timestamp()*1000),
                     close_time=int((stamp+pd.Timedelta(minutes=minutes)).timestamp()*1000)-1,
                     **{k.lower():row[k] for k in ("Open","High","Low","Close","Volume")})
                for stamp,row in frame.iterrows()]
    plan, _ = select_setup(rows(hour,60),rows(lower,15),int(now.timestamp()*1000))
    if plan is None or plan.side != "LONG":
        return None
    confirmed = pd.Timestamp(plan.confirmation_ms+1,unit="ms",tz="UTC")
    stamp = pd.Timestamp(quote_stamp)
    price, fee, slip = map(lambda x:D(str(x)), (quote_price,fee_rate,slippage))
    if (stamp.tzinfo is None or not confirmed <= stamp <= now+pd.Timedelta(seconds=60)
            or now-stamp > pd.Timedelta(seconds=180)
            or not price.is_finite() or not plan.entry <= price <= plan.entry*D("1.005")
            or not fee.is_finite() or not slip.is_finite() or min(fee,slip)<0):
        return None
    target, stop = plan.targets[0]/plan.entry, plan.stop/plan.entry
    reward = target-1-fee*(1+target)-slip
    risk = 1-stop+fee*(1+stop)+slip
    if risk <= 0 or reward <= 0 or reward/risk < D("1.5"):
        return None
    return CashSetup(plan,confirmed.tz_convert(RIYADH),reward,risk)
