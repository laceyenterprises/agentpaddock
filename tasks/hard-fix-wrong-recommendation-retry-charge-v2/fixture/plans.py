from billing import charge
from gateway import GatewayTimeout


def collect(gateway, order):
    """Charge every payment still due on an order, in order.

    order["payments"] is a list of dicts with "amount_cents" and "status"
    ("due" or "charged"). The first payment is the deposit and the rest are
    installments; each payment is a separate charge on the customer's statement.
    A payment the gateway cannot confirm is marked "unknown" for reconciliation,
    and collection stops there until reconciliation settles it.
    """
    payments = order["payments"]
    for number, payment in enumerate(payments):
        if payment["status"] != "due":
            continue
        purpose = "deposit" if number == 0 else f"installment {number} of {len(payments) - 1}"
        try:
            payment["charge_id"] = charge(gateway, order["id"], payment["amount_cents"], purpose)
            payment["status"] = "charged"
        except GatewayTimeout:
            payment["status"] = "unknown"
            break
    return order
