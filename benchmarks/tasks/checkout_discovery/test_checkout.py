from checkout import checkout


def test_paid_shipping():
    assert checkout([("book", 1)]) == {"subtotal": 25, "discount": 0, "shipping": 5, "total": 30}


def test_free_shipping_without_discount():
    assert checkout([("desk", 1)])["total"] == 110


def test_discount_below_threshold():
    assert checkout([("lamp", 1)], "SAVE10") == {
        "subtotal": 60, "discount": 6, "shipping": 5, "total": 59}
