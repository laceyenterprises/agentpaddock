def charge(gateway, order_id, amount_cents, purpose):
    """Charge one payment on an order and return the gateway's charge id.

    purpose names the payment, such as "deposit" or "installment 2 of 3", and is
    shown on the customer's statement. Raises GatewayTimeout when the gateway
    cannot confirm the charge; plans.collect handles that case.
    """
    return gateway.create_charge(order_id, amount_cents, description=purpose)
