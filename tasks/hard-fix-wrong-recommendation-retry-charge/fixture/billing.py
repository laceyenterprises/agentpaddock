def charge(gateway, order):
    """Charge an order once and return the gateway's charge id.

    order is a dict with "id" and "amount_cents". Raises GatewayTimeout when the
    gateway cannot confirm the charge; settlement.settle handles that case.
    """
    return gateway.create_charge(order["id"], order["amount_cents"])
