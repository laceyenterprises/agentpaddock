import unittest
from datetime import datetime, time

from deployer import may_deploy
from freeze import in_freeze


class Regression(unittest.TestCase):
    def test_overnight_window_covers_both_sides_of_midnight(self):
        start, end = time(22, 0), time(6, 0)
        for frozen in (time(22, 0), time(23, 30), time(0, 0), time(2, 0), time(5, 59)):
            self.assertTrue(in_freeze(frozen, start, end), frozen)
        for open_ in (time(6, 0), time(12, 0), time(21, 59)):
            self.assertFalse(in_freeze(open_, start, end), open_)

    def test_start_equal_to_end_never_freezes(self):
        # policy.py: a team that never freezes sets start == end.
        for edge in (time(0, 0), time(7, 0)):
            for now in (time(0, 0), time(3, 0), time(7, 0), time(23, 59)):
                self.assertFalse(in_freeze(now, edge, edge), (edge, now))
        self.assertTrue(may_deploy("search", datetime(2026, 10, 6, 3, 0)))


if __name__ == "__main__":
    unittest.main()
