from calculator import add

def test_negative():
    assert add(-5, 2) == -3

def test_zero():
    assert add(0, 0) == 0

def test_float():
    assert add(1.5, 2.25) == 3.75
