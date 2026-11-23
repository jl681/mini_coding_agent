from storefront.service import quote_checkout


def checkout(items, coupon=None):
    """Return the purchase receipt for the supplied cart and coupon."""
    return quote_checkout(items, coupon)
