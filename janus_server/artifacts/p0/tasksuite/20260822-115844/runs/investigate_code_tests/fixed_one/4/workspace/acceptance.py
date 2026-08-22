from pathlib import Path

from slug import slugify


assert slugify("  Hello,   World!! ") == "hello-world"
assert slugify("---A___B---") == "a-b"
assert slugify("!!!") == "item"
tests = Path("tests/test_slug.py").read_text(encoding="utf-8")
assert tests.count("def test_") >= 3, "documented edge cases need regression tests"
print("acceptance OK")
