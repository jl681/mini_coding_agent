from discounts import discounted_total
from receipts import make_receipt
from shipping import calculate_shipping


def quote_order(subtotal, coupon=None):
    merchandise_total = discounted_total(subtotal, coupon)
    shipping_fee = calculate_shipping(subtotal)
    return make_receipt(subtotal, merchandise_total, shipping_fee)
