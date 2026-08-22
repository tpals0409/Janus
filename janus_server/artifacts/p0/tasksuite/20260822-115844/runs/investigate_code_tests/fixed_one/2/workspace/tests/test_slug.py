import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual("hello-world", slugify("Hello World"))

    def test_trims_leading_trailing_separators(self):
        self.assertEqual("hello-world", slugify("  Hello,   World!! "))

    def test_replaces_runs_of_non_alphanumerics(self):
        self.assertEqual("a-b", slugify("---A___B---"))

    def test_non_alphanumeric_only_returns_item(self):
        self.assertEqual("item", slugify("!!!"))


if __name__ == "__main__":
    unittest.main()
