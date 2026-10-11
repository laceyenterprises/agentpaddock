import unittest

from subscribers import address_key


class Regression(unittest.TestCase):
    # Tests the alternative fix's own helper; the reference fix has none.
    def test_address_key(self):
        self.assertEqual(address_key("  Ann@Example.com "), "ann@example.com")


if __name__ == "__main__":
    unittest.main()
