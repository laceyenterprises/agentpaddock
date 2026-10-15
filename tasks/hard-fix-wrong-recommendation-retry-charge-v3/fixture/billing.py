def charge(gateway, order_id, amount_cents, purpose, payment_id):
    """Submit one payment and return its gateway charge id.

    plans.py owns payment identity and reconciliation. Pass purpose through as
    the statement description. Other gateway exceptions propagate immediately.
    """
    return gateway.create_charge(order_id, amount_cents, description=purpose)
