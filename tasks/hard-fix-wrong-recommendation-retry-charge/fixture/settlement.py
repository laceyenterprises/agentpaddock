from billing import charge
from gateway import GatewayTimeout


def settle(gateway, order):
    """Charge one order and record the outcome on it."""
    try:
        order["charge_id"] = charge(gateway, order)
        order["status"] = "charged"
    except GatewayTimeout:
        # Reconciliation looks the charge up by order id the next morning.
        order["status"] = "unknown"
    return order
