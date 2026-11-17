"""Checks on the pre-send check itself.

Run from the repository root:

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import build_templates as bt  # noqa: E402
import pre_send_check as psc  # noqa: E402

UNSUB = '<a href="{% unsubscribe %}">Disiscriviti</a>'
LINK = '<a href="https://shop.example/collections/all">Vai</a>'


def problems(body: str = "", subject: str = "Oggetto breve", preheader: str = "Un'anteprima diversa",
             unsub: str = UNSUB) -> list[str]:
    html = f"<p>{body}</p>{LINK}{unsub}"
    return psc.check(subject, preheader, html, f"{body} {unsub}")


class TestTheSevenFlowEmails(unittest.TestCase):
    def test_every_flow_email_is_ready_to_paste(self):
        for e in bt.EMAILS:
            with self.subTest(e["key"]):
                self.assertEqual(psc.check(e["subject"], e["preheader"], bt.render_html(e), bt.render_text(e)), [])


class TestWhatItCatches(unittest.TestCase):
    def test_a_clean_email_passes(self):
        self.assertEqual(problems("Ciao"), [])

    def test_merge_tag_without_fallback(self):
        self.assertIn("{{ first_name }} has no fallback", problems("Ciao {{ first_name }},"))

    def test_merge_tag_with_an_empty_fallback(self):
        # The bug the live templates had: "Ciao ," for a profile with no name.
        found = problems("Ciao {{ first_name|default:'' }},")
        self.assertEqual(len(found), 1)
        self.assertIn("falls back to nothing", found[0])

    def test_guarded_or_defaulted_merge_tags_pass(self):
        self.assertEqual(problems("Ciao{% if first_name %} {{ first_name }}{% endif %},"), [])
        self.assertEqual(problems("Hai preso {{ person.last_product|default:'un nostro caffè' }}."), [])

    def test_the_guard_must_test_the_same_variable(self):
        self.assertTrue(problems("{% if email %}{{ first_name }}{% endif %}"))

    def test_links_that_go_nowhere(self):
        for href in ("#", "", "http://shop.example", "/collections/all"):
            with self.subTest(href=href):
                self.assertTrue(problems(f'<a href="{href}">x</a>'))

    def test_hand_typed_utms_must_follow_the_taxonomy(self):
        bad = "https://shop.example/?utm_source=facebook&utm_medium=email&utm_campaign=lifecycle_winback"
        good = "https://shop.example/?utm_source=klaviyo&utm_medium=email&utm_campaign=lifecycle_winback"
        self.assertTrue(problems(f'<a href="{bad}">x</a>'))
        self.assertEqual(problems(f'<a href="{good}">x</a>'), [])

    def test_missing_unsubscribe(self):
        self.assertTrue(problems(unsub=""))

    def test_subject_length(self):
        self.assertEqual(problems(subject="x" * psc.MAX_SUBJECT), [])
        self.assertTrue(problems(subject="x" * (psc.MAX_SUBJECT + 1)))

    def test_preview_text_must_add_something(self):
        self.assertTrue(problems(subject="Reso gratuito", preheader="Reso gratuito."))
        self.assertTrue(problems(preheader=""))


if __name__ == "__main__":
    unittest.main()
