import unittest

from contacts import dedupe


class Regression(unittest.TestCase):
    def test_dedupe_runs(self):
        dedupe([{"email": "Ann@Example.com"}, {"email": "ann@example.com"}])


if __name__ == "__main__":
    unittest.main()
