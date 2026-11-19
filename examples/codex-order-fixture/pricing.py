"""Pricing helpers for the order-processing fixture."""

TAX_RATE = 0.08


def line_total(unit_price: float, quantity: int) -> float:
    return unit_price * quantity


def apply_discount(subtotal: float, percent: float) -> float:
    # BUG: subtracts the raw percent instead of a percentage of the subtotal.
    return subtotal - percent


def with_tax(amount: float) -> float:
    return round(amount * (1 + TAX_RATE), 2)
