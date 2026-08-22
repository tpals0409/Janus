def subtotal(lines: list[tuple[int, int]]) -> int:
    return sum(price * quantity for price, quantity in lines)
