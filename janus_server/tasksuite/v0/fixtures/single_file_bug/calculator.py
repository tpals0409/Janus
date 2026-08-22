def clamp(value: int, lower: int, upper: int) -> int:
    """Return value constrained to the inclusive [lower, upper] range."""
    return min(lower, max(value, upper))
