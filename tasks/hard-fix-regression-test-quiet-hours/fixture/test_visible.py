import unittest
from datetime import time

from quiet import in_quiet_hours


class Visible(unittest.TestCase):
    def test_daytime_window(self):
        self.assertTrue(in_quiet_hours(time(12, 0), time(9, 0), time(17, 0)))
        self.assertFalse(in_quiet_hours(time(18, 0), time(9, 0), time(17, 0)))


if __name__ == "__main__":
    unittest.main()
