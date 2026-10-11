import unittest
from datetime import time

from freeze import in_freeze


class Regression(unittest.TestCase):
    def test_overnight_window_runs(self):
        in_freeze(time(23, 30), time(22, 0), time(6, 0))


if __name__ == "__main__":
    unittest.main()
