PRICES = {"book": 25, "lamp": 60, "desk": 110}


def merchandise_subtotal(items):
    """Sum (product_name, quantity) lines before discounts and shipping."""
    return sum(PRICES[name] * quantity for name, quantity in items)
