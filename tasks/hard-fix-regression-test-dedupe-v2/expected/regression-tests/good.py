import copy
import unittest

from newsletter import send_list
from subscribers import unique


class Regression(unittest.TestCase):
    def test_case_and_surrounding_whitespace_are_one_address(self):
        subscribers = [
            {"id": 1, "email": "Ann@Example.com"},
            {"id": 2, "email": "ann@example.com"},
            {"id": 3, "email": "  ann@example.com "},
            {"id": 4, "email": "bob@example.com"},
        ]
        before = copy.deepcopy(subscribers)
        kept = unique(subscribers)
        self.assertEqual([subscriber["id"] for subscriber in kept], [1, 4])
        # newsletter.py: the CRM sync must see the subscriber dicts unchanged.
        self.assertEqual(subscribers, before)

    def test_addresses_go_out_as_typed(self):
        subscribers = [{"email": " Ann@Example.com"}, {"email": "ann@example.com"}]
        self.assertEqual(send_list(subscribers), [" Ann@Example.com"])


if __name__ == "__main__":
    unittest.main()
