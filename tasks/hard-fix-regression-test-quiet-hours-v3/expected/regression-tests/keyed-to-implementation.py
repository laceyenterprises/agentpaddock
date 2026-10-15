import unittest
from datetime import time

from freeze import wraps_midnight


class Regression(unittest.TestCase):
    # Tests the alternative fix's own helper; the reference fix has none.
    def test_overnight_window_wraps(self):
        self.assertTrue(wraps_midnight(time(22, 0), time(6, 0)))


if __name__ == "__main__":
    unittest.main()
