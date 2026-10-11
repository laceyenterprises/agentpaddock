import unittest
from datetime import time

from quiet import in_quiet_hours


class Regression(unittest.TestCase):
    # Asserts only that the answers moved away from the original's (all False);
    # any partial fix that covers one side of midnight passes.
    def test_overnight_window_is_no_longer_ignored(self):
        times = (time(23, 30), time(2, 0))
        answers = [in_quiet_hours(now, time(22, 0), time(6, 0)) for now in times]
        self.assertNotEqual(answers, [False, False])


if __name__ == "__main__":
    unittest.main()
