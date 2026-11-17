"""The browser page's functions, and files saved the way real tools save them.

Run from the repository root:

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sync_klaviyo as sk  # noqa: E402
import web_app  # noqa: E402

ORDERS = ROOT / "data" / "shopify_orders_example.csv"
CONSENT = ROOT / "data" / "consent_example.csv"


class TestFilesAsRealToolsSaveThem(unittest.TestCase):
    """Italian Excel saves with semicolons, decimal commas and DD/MM/YYYY dates."""

    def write(self, text: str, encoding: str = "utf-8") -> Path:
        d = tempfile.mkdtemp()
        path = Path(d) / "orders.csv"
        path.write_text(text, encoding=encoding)
        return path

    def test_semicolons_decimal_commas_and_day_first_dates(self):
        path = self.write("email;order_date;order_value_eur\n"
                          "a@example.com;20/05/2026;49,90\n"
                          "a@example.com;2026-05-25 10:00;1.234,50\n")
        a = sk.read_orders(path)["a@example.com"]
        self.assertEqual(a["orders"], 2)
        self.assertAlmostEqual(a["monetary"], 1284.40)
        self.assertEqual(a["last"], date(2026, 5, 25))

    def test_byte_order_mark_and_tabs(self):
        path = self.write("email\torder_date\torder_value_eur\na@example.com\t2026-05-20\t10\n", "utf-8-sig")
        self.assertEqual(sk.read_orders(path)["a@example.com"]["orders"], 1)

    def test_amounts(self):
        for text, value in (("49.90", 49.9), ("49,90", 49.9), ("€ 1.234,56", 1234.56),
                            ("1,234.56", 1234.56), ("", 0.0)):
            with self.subTest(text=text):
                self.assertAlmostEqual(sk.parse_amount(text), value)

    def test_a_customer_id_file_works_for_timing(self):
        path = self.write("customer_id,order_date,order_value_gbp\n17850,2010-12-01,15.30\n")
        self.assertEqual(list(sk.order_rows(path)), [("17850", "2010-12-01", "15.30")])


class TestPageFunctions(unittest.TestCase):
    def test_lifecycle_returns_tables_and_downloads(self):
        out = json.loads(web_app.lifecycle(str(ORDERS), str(CONSENT), "2026-06-01"))
        self.assertEqual(out["report"]["eligible"], 4)
        self.assertEqual(out["suppressed"], [["optedout@example.com", "opted_out"]])
        self.assertTrue(out["profiles_csv"].startswith("email,lifecycle_stage,"))
        self.assertEqual(len(out["profiles"]), 4)

    def test_check_template_reports_the_problems(self):
        out = json.loads(web_app.check_template('<p>Ciao {{ first_name|default:\'\' }},</p><a href="#">x</a>',
                                                "Oggetto", "Anteprima"))
        self.assertEqual(len(out["problems"]), 3)  # empty fallback, dead link, no unsubscribe

    def test_timing_on_a_file_without_a_full_year(self):
        out = json.loads(web_app.timing(str(ORDERS)))
        self.assertEqual(out["customers"], 0)

    def test_an_unreadable_file_is_a_message_not_a_crash(self):
        d = tempfile.mkdtemp()
        bad = Path(d) / "orders.csv"
        bad.write_text("email,order_date\na@example.com,May 20\n", encoding="utf-8")
        out = json.loads(web_app.timing(str(bad)))
        self.assertIn("neither YYYY-MM-DD nor DD/MM/YYYY", out["error"])

    def test_suppression_list_cannot_carry_a_formula(self):
        self.assertEqual(sk.safe_cell("=cmd@example.com"), "'=cmd@example.com")


if __name__ == "__main__":
    unittest.main()
