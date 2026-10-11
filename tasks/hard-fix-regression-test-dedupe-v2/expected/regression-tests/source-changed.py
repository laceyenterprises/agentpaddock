import unittest

from subscribers import unique


class Regression(unittest.TestCase):
    # Asserts only that the output moved away from the original's; any fix that
    # drops one duplicate passes.
    def test_duplicates_are_no_longer_all_kept(self):
        subscribers = [
            {"id": 1, "email": "Ann@Example.com"},
            {"id": 2, "email": "ann@example.com"},
            {"id": 3, "email": "  ann@example.com "},
        ]
        self.assertNotEqual(len(unique(subscribers)), len(subscribers))


if __name__ == "__main__":
    unittest.main()
