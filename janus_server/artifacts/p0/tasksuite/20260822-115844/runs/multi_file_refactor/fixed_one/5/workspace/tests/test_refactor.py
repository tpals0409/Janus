import inspect
import unittest

from invoice import report


class RefactorTests(unittest.TestCase):
    def test_behavior_is_preserved(self):
        lines = [(120, 2), (30, 3)]
        self.assertEqual(330, report.subtotal(lines))
        self.assertEqual("TOTAL=330", report.render_invoice(lines))

    def test_subtotal_is_defined_in_totals_module(self):
        from invoice import totals

        self.assertEqual("invoice.totals", totals.subtotal.__module__)
        self.assertIs(report.subtotal, totals.subtotal)
        self.assertNotIn("def subtotal", inspect.getsource(report))


if __name__ == "__main__":
    unittest.main()
