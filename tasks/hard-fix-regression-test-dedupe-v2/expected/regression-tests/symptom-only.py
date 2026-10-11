import unittest

from subscribers import unique


class Regression(unittest.TestCase):
    # Pins the reported duplicates only. It compares against the same dicts it
    # passed in, so a fix that rewrites their addresses in place still passes.
    def test_case_and_surrounding_whitespace_are_one_address(self):
        subscribers = [
            {"id": 1, "email": "Ann@Example.com"},
            {"id": 2, "email": "ann@example.com"},
            {"id": 3, "email": "  ann@example.com "},
            {"id": 4, "email": "bob@example.com"},
        ]
        self.assertEqual(unique(subscribers), [subscribers[0], subscribers[3]])


if __name__ == "__main__":
    unittest.main()
