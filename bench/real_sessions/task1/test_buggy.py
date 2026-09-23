from buggy import refund


def test_discount_applied_once():
    order = {"amount": 100, "discount": 10}
    result = refund(order)
    assert result == 90, f"Expected 90, got {result}"


def test_no_discount():
    order = {"amount": 50}
    result = refund(order)
    assert result == 50
