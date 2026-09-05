import importlib.util
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "skills" / "stock-character-analysis" / "scripts" / "analyze_stock_character.py"
SPEC = importlib.util.spec_from_file_location("stock_character", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_bars(multiplier):
    bars = []
    value = 10.0
    for index in range(120):
        previous = value
        value *= multiplier(index)
        bars.append({"date": date(2026, 1, 1) + timedelta(days=index), "open": previous, "close": value, "high": max(previous, value) * 1.01, "low": min(previous, value) * 0.99, "volume": 1_000_000, "amount": 200_000_000, "turnover_rate": 2.0})
    return bars


class StockCharacterAnalysisTest(unittest.TestCase):
    def test_stable_trend_label(self):
        result = MODULE.analyze("000001", "样例", make_bars(lambda _: 1.006), "test")
        self.assertIn("稳定趋势", result["labels"])
        self.assertGreater(result["metrics"]["direction_efficiency_20d"], 0.9)

    def test_short_history_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.analyze("000001", "样例", make_bars(lambda _: 1.001)[:20], "test")

    def test_volatile_series_is_high_elasticity(self):
        bars = make_bars(lambda index: 1 + (0.06 if index % 2 == 0 else -0.055))
        result = MODULE.analyze("000001", "样例", bars, "test")
        self.assertIn("高弹性", result["labels"])


if __name__ == "__main__":
    unittest.main()
