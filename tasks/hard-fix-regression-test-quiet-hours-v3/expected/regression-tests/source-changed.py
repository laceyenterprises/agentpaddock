import unittest
from datetime import datetime, time
from freeze import in_freeze


class Regression(unittest.TestCase):
    def test_policy(self):
        answers = [
            in_freeze(datetime(2026, 10, 5, hour), time(22), time(6), (0,))
            for hour in (23, 2)
        ]
        self.assertNotEqual(answers, [False, False])
