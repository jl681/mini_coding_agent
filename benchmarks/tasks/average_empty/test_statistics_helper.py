from statistics_helper import average


def test_nonempty_average():
    assert average([2, 4, 6]) == 4
