import unittest

from contacts import canonical_email


class Regression(unittest.TestCase):
    # Tests the alternative fix's own helper; the reference fix has none.
    def test_canonical_email(self):
        self.assertEqual(canonical_email("  Ann@Example.com "), "ann@example.com")


if __name__ == "__main__":
    unittest.main()
