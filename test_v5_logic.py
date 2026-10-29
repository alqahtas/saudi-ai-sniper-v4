import unittest
import numpy as np
import pandas as pd
from sniper_v5 import indicators, signals, analyze, evaluate

class Tests(unittest.TestCase):
    def setUp(self):
        ix=pd.bdate_range('2024-01-01',periods=300)
        c=np.linspace(70,100,300)
        self.raw=pd.DataFrame({'Open':c-.1,'High':c+1,'Low':c-1,'Close':c,'Volume':np.full(300,4000000)},index=ix)

    def test_historical_evaluate_last_full_window(self):
        d=indicators(self.raw)
        # exactly 15 future bars remain; evaluation must be allowed
        self.assertIsNotNone(evaluate(d, len(d)-16, holding_bars=15))

    def test_reference_fields(self):
        output=analyze('2010',self.raw)
        for key in ('entry_reference','stop_reference','target1_reference','target2_reference'):
            self.assertIn(key,output)
        self.assertIn('backtests',output)

    def test_ohlcv_no_lookahead_levels(self):
        d=indicators(self.raw)
        self.assertAlmostEqual(d['resistance20'].iloc[-1], self.raw['High'].iloc[-21:-1].max())

if __name__=='__main__': unittest.main()
