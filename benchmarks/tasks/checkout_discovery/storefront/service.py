from catalog import merchandise_subtotal
from policies.order_quote import quote_order


def quote_checkout(items, coupon=None):
    subtotal = merchandise_subtotal(items)
    return quote_order(subtotal, coupon)
