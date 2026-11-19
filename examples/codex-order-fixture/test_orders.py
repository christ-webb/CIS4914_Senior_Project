import unittest

from orders import count_units, order_subtotal, order_total

ITEMS = [{"price": 10.0, "qty": 2}, {"price": 5.0, "qty": 4}]


class OrderTests(unittest.TestCase):
    def test_subtotal(self) -> None:
        self.assertEqual(order_subtotal(ITEMS), 40.0)

    def test_total_without_discount(self) -> None:
        self.assertEqual(order_total(ITEMS), 43.2)

    def test_total_with_discount(self) -> None:  # fails until apply_discount is fixed
        self.assertEqual(order_total(ITEMS, discount_percent=25), 32.4)

    def test_count_units(self) -> None:  # fails until count_units is fixed
        self.assertEqual(count_units(ITEMS), 6)


if __name__ == "__main__":
    unittest.main()
