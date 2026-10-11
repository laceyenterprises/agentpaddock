import pathlib
import unittest


class Regression(unittest.TestCase):
    def test_fix_is_present(self):
        self.assertIn(" or ", pathlib.Path("quiet.py").read_text())


if __name__ == "__main__":
    unittest.main()
