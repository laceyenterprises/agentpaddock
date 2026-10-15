import unittest
from datetime import datetime, timedelta, time
from freeze import in_freeze
from deployer import may_deploy


class Regression(unittest.TestCase):
    def test_policy(self):
        origin = datetime(2026, 10, 5)
        for day in range(7):
            for hour in (0, 2, 6, 12, 22, 23):
                elapsed = day * 24 + hour
                expected = any(
                    d * 24 + 22 <= elapsed < d * 24 + 30 for d in (-1, 0, 4, 6)
                )
                now = origin + timedelta(days=day, hours=hour)
                self.assertEqual(in_freeze(now, time(22), time(6), (0, 4, 6)), expected)
        self.assertTrue(
            in_freeze(datetime(2026, 10, 6, 5, 59, 59, 999999), time(22), time(6), (0,))
        )
        self.assertFalse(in_freeze(origin, time(0), time(0)))
        self.assertTrue(may_deploy("maple", datetime(2026, 10, 10, 2), hotfix=True))


if __name__ == "__main__":
    unittest.main()
