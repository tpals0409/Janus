from invoice.totals import subtotal


def render_invoice(lines: list[tuple[int, int]]) -> str:
    return f"TOTAL={subtotal(lines)}"
