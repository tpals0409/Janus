def subtotal(lines: list[tuple[int, int]]) -> int:
    return sum(price * quantity for price, quantity in lines)


def render_invoice(lines: list[tuple[int, int]]) -> str:
    return f"TOTAL={subtotal(lines)}"
