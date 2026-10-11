import unittest

from newsletter import send_list
from subscribers import unique


class Visible(unittest.TestCase):
    def test_distinct_addresses_are_kept(self):
        subscribers = [{"email": "a@example.com"}, {"email": "b@example.com"}]
        self.assertEqual(unique(subscribers), subscribers)
        self.assertEqual(send_list(subscribers), ["a@example.com", "b@example.com"])


if __name__ == "__main__":
    unittest.main()
