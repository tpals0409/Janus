import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual("hello-world", slugify("Hello World"))

    def test_mixed_separators_and_punctuation(self):
        self.assertEqual("hello-world", slugify("  Hello,   World!! "))

    def test_runs_of_separators_collapsed(self):
        self.assertEqual("a-b", slugify("---A___B---"))

    def test_no_alphanumerics_returns_item(self):
        self.assertEqual("item", slugify("!!!"))


if __name__ == "__main__":
    unittest.main()
