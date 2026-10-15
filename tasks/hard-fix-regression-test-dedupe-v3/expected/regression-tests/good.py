import copy
import unittest
from subscribers import unique
from newsletter import send_list


class Regression(unittest.TestCase):
    def test_selection(self):
        rows = [
            {"id": 1, "list_id": "red|blue", "email": " A@Example.test "},
            {"id": 2, "list_id": "red|blue", "email": "a@example.test"},
            {"id": 3, "list_id": "red", "email": "blue|a@example.test"},
            {"id": 4, "list_id": "green", "email": "a@example.test"},
            {"id": 5},
            {"id": 6, "email": None},
        ]
        before = copy.deepcopy(rows)
        kept = unique(iter(rows))
        self.assertEqual([r["id"] for r in kept], [1, 3, 4, 5, 6])
        self.assertTrue(
            all(a is b for a, b in zip(kept, [rows[i] for i in (0, 2, 3, 4, 5)]))
        )
        self.assertEqual(rows, before)
        self.assertEqual(
            send_list(iter(rows)),
            [
                ("red|blue", " A@Example.test "),
                ("red", "blue|a@example.test"),
                ("green", "a@example.test"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
