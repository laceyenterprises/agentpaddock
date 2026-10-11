import hashlib

from gateway import GatewayTimeout


def payment_key(order_id, purpose):
    """One idempotency key per payment, shared by every attempt at it."""
    return hashlib.sha256(f"{order_id}\0{purpose}".encode()).hexdigest()


def charge(gateway, order_id, amount_cents, purpose):
    """Charge one payment on an order and return the gateway's charge id.

    purpose names the payment, such as "deposit" or "installment 2 of 3", and is
    shown on the customer's statement. Raises GatewayTimeout when the gateway
    cannot confirm the charge; plans.collect handles that case.
    """
    key = payment_key(order_id, purpose)
    last = None
    for _ in range(3):
        try:
            return gateway.create_charge(
                order_id, amount_cents, description=purpose, idempotency_key=key
            )
        except GatewayTimeout as exc:
            last = exc
    raise last
