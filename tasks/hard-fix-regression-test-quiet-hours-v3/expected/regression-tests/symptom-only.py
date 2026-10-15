import unittest
from datetime import datetime, time
from freeze import in_freeze


class Regression(unittest.TestCase):
    def test_policy(self):
        self.assertTrue(in_freeze(datetime(2026, 10, 5, 23), time(22), time(6), (0,)))
