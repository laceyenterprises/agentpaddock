import unittest
from datetime import time

from freeze import in_freeze


class Regression(unittest.TestCase):
    # Pins the reported overnight window and nothing else, so the plausible
    # fix that freezes start == end windows all day passes it.
    def test_overnight_window_covers_both_sides_of_midnight(self):
        start, end = time(22, 0), time(6, 0)
        for frozen in (time(22, 0), time(23, 30), time(0, 0), time(2, 0), time(5, 59)):
            self.assertTrue(in_freeze(frozen, start, end), frozen)
        for open_ in (time(6, 0), time(12, 0), time(21, 59)):
            self.assertFalse(in_freeze(open_, start, end), open_)


if __name__ == "__main__":
    unittest.main()
