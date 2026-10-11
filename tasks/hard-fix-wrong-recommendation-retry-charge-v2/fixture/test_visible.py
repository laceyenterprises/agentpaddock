import unittest

from plans import collect


class OneShotGateway:
    def __init__(self):
        self.charges = []

    def create_charge(self, order_id, amount_cents, description="", idempotency_key=None):
        self.charges.append((order_id, amount_cents, description))
        return f"ch_{len(self.charges)}"


class Visible(unittest.TestCase):
    def test_deposit_only_order_charges_once(self):
        gateway = OneShotGateway()
        order = collect(
            gateway, {"id": "o-1", "payments": [{"amount_cents": 1250, "status": "due"}]}
        )
        self.assertEqual(order["payments"][0]["status"], "charged")
        self.assertEqual(order["payments"][0]["charge_id"], "ch_1")
        self.assertEqual(gateway.charges, [("o-1", 1250, "deposit")])


if __name__ == "__main__":
    unittest.main()
