from decimal import Decimal as D
import unittest

from fibonacci import confluence


HIGHS = [100, 100, 98, 102, 106, 110, 107, 105, 106]
LOWS = [97, 96, 90, 94, 99, 103, 101, 98, 100]


class FibonacciTests(unittest.TestCase):
    def test_three_retracement_ratios_and_extensions(self):
        for price, ratio in (("102.36", ".382"), ("100", ".50"), ("97.64", ".618")):
            with self.subTest(ratio=ratio):
                result = confluence(HIGHS, LOWS, "LONG", price)
                self.assertEqual((result.ratio, result.retracement), (D(ratio), D(price)))
                self.assertEqual(result.extensions, (D("117.64"), D("120"), D("122.36")))

    def test_short_mirrors_the_swing_and_targets(self):
        result = confluence([200 - value for value in LOWS],
                            [200 - value for value in HIGHS], "SHORT", 100)
        self.assertEqual(result.ratio, D(".50"))
        self.assertEqual(result.extensions, (D("82.36"), D("80"), D("77.64")))

    def test_no_confluence_without_two_closed_neighbors_or_after_break(self):
        self.assertIsNone(confluence(HIGHS[:-2], LOWS[:-2], "LONG", 100))
        self.assertIsNone(confluence(HIGHS, LOWS, "LONG", 101))
        self.assertIsNone(confluence(HIGHS, LOWS[:-1] + [89], "LONG", 100))
