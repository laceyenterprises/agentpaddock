import unittest

from subscribers import unique


class Regression(unittest.TestCase):
    def test_unique_runs(self):
        unique([{"email": "Ann@Example.com"}, {"email": "ann@example.com"}])


if __name__ == "__main__":
    unittest.main()
