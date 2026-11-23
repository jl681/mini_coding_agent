from checkout import checkout
from discounts import discounted_total
from shipping import calculate_shipping


def test_discount_crosses_threshold():
    assert checkout([("desk", 1)], "SAVE10") == {
        "subtotal": 110, "discount": 11, "shipping": 5, "total": 104}


def test_exact_threshold_after_discount():
    assert checkout([("book", 5)], "SAVE20") == {
        "subtotal": 125, "discount": 25, "shipping": 0, "total": 100}


def test_discount_stays_above_threshold():
    assert checkout([("desk", 2)], "SAVE20") == {
        "subtotal": 220, "discount": 44, "shipping": 0, "total": 176}


def test_no_discount_exact_threshold_and_unknown_coupon():
    assert checkout([("book", 4)])["shipping"] == 0
    assert checkout([("desk", 1)], "UNKNOWN")["total"] == 110


def test_supporting_calculations_preserved():
    assert discounted_total(110, "SAVE10") == 99
    assert discounted_total(125, "SAVE20") == 100
    assert calculate_shipping(99) == 5
    assert calculate_shipping(100) == 0
