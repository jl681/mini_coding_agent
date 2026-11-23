def make_receipt(subtotal, merchandise_total, shipping_fee):
    """Keep merchandise discounts separate from shipping in the receipt."""
    return {"subtotal": subtotal, "discount": round(subtotal - merchandise_total, 2),
            "shipping": shipping_fee, "total": round(merchandise_total + shipping_fee, 2)}
