"""Order processing for the fixture."""

from pricing import apply_discount, line_total, with_tax


def order_subtotal(items: list[dict]) -> float:
    return sum(line_total(item["price"], item["qty"]) for item in items)


def order_total(items: list[dict], discount_percent: float = 0.0) -> float:
    subtotal = order_subtotal(items)
    discounted = apply_discount(subtotal, discount_percent)
    return with_tax(discounted)


def count_units(items: list[dict]) -> int:
    # BUG: counts line items rather than units.
    return len(items)
