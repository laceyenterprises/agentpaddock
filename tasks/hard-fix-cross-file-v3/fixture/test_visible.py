import unittest

from backend import NotFound
from reconcile import reconcile


class Directory:
    def __init__(self, users):
        self.users = users

    def get_user(self, user, refresh=False):
        if user not in self.users:
            raise NotFound(user)
        return self.users[user]


class Visible(unittest.TestCase):
    def test_departed_user_is_deactivated(self):
        client = Directory({"ann": {"name": "Ann"}})
        report = reconcile(client, ["ann", "bob"])
        self.assertEqual(
            report, {"deactivated": ["bob"], "kept": ["ann"], "skipped": []}
        )


if __name__ == "__main__":
    unittest.main()
