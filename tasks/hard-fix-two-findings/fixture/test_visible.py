import unittest

from pager import format_page


class Visible(unittest.TestCase):
    def test_short_page_is_unchanged(self):
        alert = {"severity": "warning", "summary": "disk 91% on db-2"}
        self.assertEqual(format_page(alert), "[warning] disk 91% on db-2")


if __name__ == "__main__":
    unittest.main()
