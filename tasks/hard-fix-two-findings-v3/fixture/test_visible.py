import unittest

from limiter import Limiter
from proxy import handle


class Visible(unittest.TestCase):
    def test_requests_under_the_limit_are_admitted(self):
        limiter = Limiter(2, 1000)
        self.assertEqual(handle(limiter, "a", 0), (200, {}))
        self.assertEqual(handle(limiter, "a", 100), (200, {}))
        self.assertEqual(handle(limiter, "b", 100), (200, {}))

    def test_request_over_the_limit_is_rejected(self):
        limiter = Limiter(1, 1000)
        handle(limiter, "a", 0)
        status, headers = handle(limiter, "a", 10)
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)


if __name__ == "__main__":
    unittest.main()
