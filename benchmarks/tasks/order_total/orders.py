from pricing import line_total


def order_total(items):
    """Sum (unit_price, quantity) lines; an empty order costs zero."""
    return sum(line_total(price, quantity) for price, quantity in items)
