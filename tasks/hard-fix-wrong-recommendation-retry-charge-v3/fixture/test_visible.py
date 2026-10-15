import unittest
from plans import collect


class OneShotGateway:
    def __init__(self):
        self.charges = []

    def create_charge(
        self, order_id, amount_cents, description="", idempotency_key=None
    ):
        self.charges.append((order_id, amount_cents, description))
        return f"ch_{len(self.charges)}"


class Visible(unittest.TestCase):
    def test_collection(self):
        gateway = OneShotGateway()
        order = collect(
            gateway,
            {
                "id": "order-a",
                "payments": [
                    {
                        "id": "payment-a",
                        "amount_cents": 1250,
                        "purpose": "deposit",
                        "status": "due",
                    }
                ],
            },
        )
        self.assertEqual(order["payments"][0]["status"], "charged")
        self.assertEqual(gateway.charges, [("order-a", 1250, "deposit")])


if __name__ == "__main__":
    unittest.main()
