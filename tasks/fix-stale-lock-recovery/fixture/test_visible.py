import unittest
import target
from support import FakeFS


class Visible(unittest.TestCase):
    def test_smoke(self):
        fs = FakeFS()
        self.assertFalse(target.recover(fs, "lock", 10))
        self.assertEqual(fs.owner, "orphan")


if __name__ == "__main__":
    unittest.main()
