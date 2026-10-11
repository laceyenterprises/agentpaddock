import inspect
import unittest

import subscribers


class Regression(unittest.TestCase):
    def test_fix_is_present(self):
        self.assertIn("strip", inspect.getsource(subscribers.unique))


if __name__ == "__main__":
    unittest.main()
