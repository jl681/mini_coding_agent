from catalog import merchandise_subtotal
from receipts import make_receipt
from shipping import calculate_shipping


def preview_cart(items):
    """Estimate a cart before a coupon has been entered."""
    subtotal = merchandise_subtotal(items)
    return make_receipt(subtotal, subtotal, calculate_shipping(subtotal))
