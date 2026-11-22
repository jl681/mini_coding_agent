from statistics_helper import average


def test_empty_list():
    assert average([]) == 0


def test_empty_tuple():
    assert average(()) == 0


def test_negative_values():
    assert average([-5, -1]) == -3


def test_fractional_average():
    assert average([1, 2]) == 1.5
