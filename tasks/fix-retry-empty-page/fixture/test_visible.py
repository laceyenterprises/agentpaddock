import unittest
import target
from support import Client, PageStore


class Visible(unittest.TestCase):
    def test_smoke(self):
        store = PageStore()
        self.assertEqual(target.drain(Client(["row"]), store), ["row"])
        self.assertEqual(store.cursor, 8)


if __name__ == "__main__":
    unittest.main()
