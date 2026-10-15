from billing import charge
from gateway import GatewayTimeout


def collect(gateway, order):
    """Collect due payments in order, stopping at an uncertain result.

    order.id and each payment.id are opaque strings, including punctuation and
    Unicode. A payment id is unique within its order, immutable, and retained
    when an uncertain payment is reconciled and put back to due. Descriptions
    may change between collection runs, and distinct payments may have equal
    descriptions and amounts. A reconciled retry is the same payment even
    after a process restart. Different orders may reuse payment ids.
    Each payment is one charge; on a timeout mark it unknown and stop.
    """
    for payment in order["payments"]:
        if payment["status"] != "due":
            continue
        try:
            payment["charge_id"] = charge(
                gateway,
                order["id"],
                payment["amount_cents"],
                payment["purpose"],
                payment["id"],
            )
            payment["status"] = "charged"
        except GatewayTimeout:
            payment["status"] = "unknown"
            break
    return order
