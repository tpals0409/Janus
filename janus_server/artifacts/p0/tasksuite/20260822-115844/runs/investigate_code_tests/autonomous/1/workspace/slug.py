import re


def slugify(text: str) -> str:
    text = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or "item"
