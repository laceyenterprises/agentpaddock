import unittest
from newsletter import send_list
from subscribers import unique


class Visible(unittest.TestCase):
    def test_selection(self):
        rows = [{"email": "a@example.test"}, {"email": "b@example.test"}]
        self.assertEqual(unique(rows), rows)
        self.assertEqual(
            send_list(rows), [("", "a@example.test"), ("", "b@example.test")]
        )


if __name__ == "__main__":
    unittest.main()
