import unittest
from subscribers import unique


class Regression(unittest.TestCase):
    def test_selection(self):
        rows = [{"email": " A@Example.test "}, {"email": "a@example.test"}]
        self.assertEqual(len(unique(rows)), 1)
