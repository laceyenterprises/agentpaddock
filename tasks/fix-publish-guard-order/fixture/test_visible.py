import unittest
import target
from support import Store


class Visible(unittest.TestCase):
    def test_smoke(self):
        store = Store(True)
        self.assertEqual(target.publish(store, "a", "payload"), "receipt")


if __name__ == "__main__":
    unittest.main()
