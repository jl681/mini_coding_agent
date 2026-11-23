from settings import FREE_SHIPPING_THRESHOLD, STANDARD_SHIPPING_FEE


def calculate_shipping(merchandise_total):
    """Shipping is free at or above the eligible merchandise threshold."""
    return 0 if merchandise_total >= FREE_SHIPPING_THRESHOLD else STANDARD_SHIPPING_FEE
