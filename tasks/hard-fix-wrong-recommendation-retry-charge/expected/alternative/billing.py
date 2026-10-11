import uuid

from gateway import GatewayTimeout


def charge(gateway, order):
    """Charge an order once and return the gateway's charge id.

    order is a dict with "id" and "amount_cents". Raises GatewayTimeout when the
    gateway cannot confirm the charge; settlement.settle handles that case.
    """
    key = uuid.uuid4().hex
    last = None
    for _ in range(3):
        try:
            return gateway.create_charge(order["id"], order["amount_cents"], idempotency_key=key)
        except GatewayTimeout as exc:
            last = exc
    raise last
