import unittest
import target


class Visible(unittest.TestCase):
    def test_smoke(self):
        self.assertEqual(target.provision({}, {"b": "remote"}, "b", "main"), "remote")


if __name__ == "__main__":
    unittest.main()
