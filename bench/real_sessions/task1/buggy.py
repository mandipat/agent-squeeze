def refund(order):
    """Refund an order. Bug: refunds twice when order has a discount applied."""
    total = order["amount"]
    if order.get("discount"):
        total = total - order["discount"]
    order["refunded"] = total
    return total
