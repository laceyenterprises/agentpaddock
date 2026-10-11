import unittest
from datetime import datetime, time

from deployer import may_deploy
from freeze import in_freeze


class Visible(unittest.TestCase):
    def test_daytime_window(self):
        self.assertTrue(in_freeze(time(12, 0), time(9, 0), time(17, 0)))
        self.assertFalse(in_freeze(time(18, 0), time(9, 0), time(17, 0)))

    def test_daytime_team(self):
        self.assertFalse(may_deploy("growth", datetime(2026, 10, 6, 13, 0)))
        self.assertTrue(may_deploy("growth", datetime(2026, 10, 6, 15, 0)))
        self.assertTrue(may_deploy("growth", datetime(2026, 10, 6, 13, 0), hotfix=True))


if __name__ == "__main__":
    unittest.main()
