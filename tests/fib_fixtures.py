"""Deterministic OHLC fixtures, no network or account state."""
NOW=1_800_000_600_000


def frames(short=False):
    result={}
    for interval,duration,slope in (("1h",3_600_000,.02),("15m",900_000,.004)):
        rows=[]
        end=NOW//duration*duration
        for remaining in range(498,0,-1):
            close=99.10-(remaining-1)*slope
            start=end-remaining*duration
            rows.append(dict(open=close-.001,high=close+.015,low=close-.015,close=close,
                             volume=100,open_time=start,close_time=start+duration-1))
        result[interval]=rows
    h=result["1h"]
    h[-35]["high"]=102
    h[-25]["low"]=98
    h[-7]["high"]=102
    m=result["15m"]
    m[-15]["low"]=98.80
    m[-8]["low"]=98.75
    m[-1].update(open=99.09,close=99.10,low=98.84,high=99.12)
    if short:
        for rows in result.values():
            for r in rows:
                r.update(open=200-r["open"],high=200-r["low"],low=200-r["high"],close=200-r["close"])
    return result
