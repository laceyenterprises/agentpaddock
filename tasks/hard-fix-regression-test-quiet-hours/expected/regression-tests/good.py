import unittest
from datetime import time

from quiet import in_quiet_hours


class Regression(unittest.TestCase):
    def test_overnight_window_covers_both_sides_of_midnight(self):
        start, end = time(22, 0), time(6, 0)
        for quiet in (time(22, 0), time(23, 30), time(0, 0), time(2, 0), time(5, 59)):
            self.assertTrue(in_quiet_hours(quiet, start, end), quiet)
        for loud in (time(6, 0), time(12, 0), time(21, 59)):
            self.assertFalse(in_quiet_hours(loud, start, end), loud)


if __name__ == "__main__":
    unittest.main()
