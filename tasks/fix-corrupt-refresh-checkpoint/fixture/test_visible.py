import unittest
import tempfile
from pathlib import Path
import target


class Visible(unittest.TestCase):
    def test_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(target.load(Path(directory)), ({}, {}))


if __name__ == "__main__":
    unittest.main()
