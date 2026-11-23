from settings import DISCOUNT_RATES


def discounted_total(subtotal, coupon=None):
    """Apply a known coupon; unknown coupons leave the subtotal unchanged."""
    return round(subtotal * (1 - DISCOUNT_RATES.get(coupon, 0)), 2)
