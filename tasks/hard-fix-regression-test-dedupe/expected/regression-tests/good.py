import unittest

from contacts import dedupe


class Regression(unittest.TestCase):
    def test_case_and_surrounding_whitespace_are_one_address(self):
        records = [
            {"id": 1, "email": "Ann@Example.com"},
            {"id": 2, "email": "ann@example.com"},
            {"id": 3, "email": "  ann@example.com "},
            {"id": 4, "email": "bob@example.com"},
        ]
        kept = dedupe(records)
        self.assertEqual([record["id"] for record in kept], [1, 4])
        self.assertEqual(kept[0], {"id": 1, "email": "Ann@Example.com"})


if __name__ == "__main__":
    unittest.main()
