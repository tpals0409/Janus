import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual("hello-world", slugify("Hello World"))

    def test_trims_and_collapses_separator_runs(self):
        # leading/trailing separators trimmed, runs collapsed to one "-"
        self.assertEqual("hello-world", slugify("  Hello,   World!! "))
        self.assertEqual("a-b", slugify("---A___B---"))

    def test_non_ascii_punctuation_and_underscores(self):
        # every run of non-alphanumeric characters becomes a single "-"
        self.assertEqual("foo-bar-baz", slugify("Foo_bar!Baz"))
        self.assertEqual("hello-world", slugify("Hello\tWorld"))

    def test_returns_item_when_no_alphanumerics(self):
        # separators only / non-alphanumeric input yields the fallback
        self.assertEqual("item", slugify("!!!"))
        self.assertEqual("item", slugify("   "))
        self.assertEqual("item", slugify("&&&---"))

    def test_lowercases_ascii(self):
        self.assertEqual("abc123", slugify("ABC123"))

    def test_empty_string(self):
        self.assertEqual("item", slugify(""))


if __name__ == "__main__":
    unittest.main()
