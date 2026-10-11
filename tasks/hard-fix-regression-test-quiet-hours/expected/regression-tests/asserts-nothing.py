import unittest
from datetime import time

from quiet import in_quiet_hours


class Regression(unittest.TestCase):
    def test_overnight_window_runs(self):
        in_quiet_hours(time(23, 30), time(22, 0), time(6, 0))


if __name__ == "__main__":
    unittest.main()
