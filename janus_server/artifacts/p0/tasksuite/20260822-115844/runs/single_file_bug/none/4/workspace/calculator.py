def clamp(value: int, lower: int, upper: int) -> int:
    """Return value constrained to the inclusive [lower, upper] range."""
    return max(lower, min(value, upper))
