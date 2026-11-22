from orders import order_total
from pricing import line_total


def test_multiple_lines():
    assert order_total([(4, 3), (2, 5)]) == 22


def test_empty_order():
    assert order_total([]) == 0


def test_free_and_zero_quantity():
    assert line_total(0, 5) == 0
    assert line_total(7, 0) == 0


def test_fractional_price():
    assert order_total([(1.25, 4)]) == 5
