import inspect
import unittest

import contacts


class Regression(unittest.TestCase):
    def test_fix_is_present(self):
        self.assertIn("strip", inspect.getsource(contacts.dedupe))


if __name__ == "__main__":
    unittest.main()
