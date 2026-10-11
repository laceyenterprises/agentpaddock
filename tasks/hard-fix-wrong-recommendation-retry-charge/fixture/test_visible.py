import unittest

from settlement import settle


class OneShotGateway:
    def __init__(self):
        self.charges = []

    def create_charge(self, order_id, amount_cents, idempotency_key=None):
        self.charges.append((order_id, amount_cents))
        return f"ch_{len(self.charges)}"


class Visible(unittest.TestCase):
    def test_happy_path_charges_once(self):
        gateway = OneShotGateway()
        order = settle(gateway, {"id": "o-1", "amount_cents": 1250})
        self.assertEqual(order["status"], "charged")
        self.assertEqual(order["charge_id"], "ch_1")
        self.assertEqual(gateway.charges, [("o-1", 1250)])


if __name__ == "__main__":
    unittest.main()
