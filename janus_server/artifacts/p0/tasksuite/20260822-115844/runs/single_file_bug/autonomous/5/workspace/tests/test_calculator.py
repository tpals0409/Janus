import unittest

from calculator import clamp


class ClampTests(unittest.TestCase):
    def test_below_range(self):
        self.assertEqual(0, clamp(-5, 0, 10))

    def test_inside_range(self):
        self.assertEqual(6, clamp(6, 0, 10))

    def test_above_range(self):
        self.assertEqual(10, clamp(15, 0, 10))


if __name__ == "__main__":
    unittest.main()
