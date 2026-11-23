from receipts import make_receipt
from shipping import calculate_shipping


def quote_renewal(merchandise_total):
    """Renewals receive an already-discounted total from the subscription service."""
    shipping_fee = calculate_shipping(merchandise_total)
    return make_receipt(merchandise_total, merchandise_total, shipping_fee)
