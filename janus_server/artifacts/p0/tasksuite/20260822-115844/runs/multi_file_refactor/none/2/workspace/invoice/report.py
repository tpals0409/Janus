from invoice.totals import subtotal

__all__ = ["subtotal", "render_invoice"]


def render_invoice(lines: list[tuple[int, int]]) -> str:
    return f"TOTAL={subtotal(lines)}"
