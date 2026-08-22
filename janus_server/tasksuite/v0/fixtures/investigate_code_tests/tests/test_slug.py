import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual("hello-world", slugify("Hello World"))


if __name__ == "__main__":
    unittest.main()
