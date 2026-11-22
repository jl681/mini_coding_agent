from orders import order_total


def test_single_line():
    assert order_total([(4, 3)]) == 12
