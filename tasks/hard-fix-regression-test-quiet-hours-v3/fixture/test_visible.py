import unittest
from datetime import datetime
from deployer import may_deploy


class Visible(unittest.TestCase):
    def test_release(self):
        self.assertFalse(may_deploy("cedar", datetime(2026, 10, 5, 13)))
        self.assertTrue(may_deploy("cedar", datetime(2026, 10, 5, 15)))
        self.assertTrue(may_deploy("cedar", datetime(2026, 10, 5, 13), hotfix=True))


if __name__ == "__main__":
    unittest.main()
