import unittest
import target


class Visible(unittest.TestCase):
    def test_smoke(self):
        self.assertEqual(
            target.configure({"tags": [], "limit": 3}, "a"), {"tags": ["a"], "limit": 3}
        )


if __name__ == "__main__":
    unittest.main()
