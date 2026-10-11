import unittest

from contacts import dedupe


class Visible(unittest.TestCase):
    def test_distinct_addresses_are_kept(self):
        records = [{"email": "a@example.com"}, {"email": "b@example.com"}]
        self.assertEqual(dedupe(records), records)


if __name__ == "__main__":
    unittest.main()
