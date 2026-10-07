"""Tests for the personal results page (FR-08, NFR-10).

The page is tested as a black box through the test client; the chart geometry
helpers are pure functions and are tested directly at their boundaries.
"""

import unittest

from src.modules import results
from src.modules.scoring import CUE_LABELS
from tests.helpers import AppTestCase

NEXT_STEPS = "What to practice next"


class ResultsTests(AppTestCase):
    def finish_session(self, pre, post):
        """Run one participant through every phase with the given answer patterns."""
        self.consent()
        self.answer_pattern("/assessment/pre", pre)
        self.finish_practice()
        self.answer_pattern("/assessment/post", post)
        return self.client.get("/results")

    def test_results_are_locked_until_the_post_test_is_complete(self):
        self.reach_posttest()
        response = self.client.get("/results")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_results_compare_both_assessments(self):
        response = self.finish_session([True] * 7 + [False] * 5, [True] * 10 + [False] * 2)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for expected in ("58.3%", "83.3%", "+25.0 points"):
            self.assertIn(expected, page)

    def test_chart_uses_svg_attributes_and_no_inline_styles(self):
        response = self.finish_session([False] * 12, [True] * 12)
        page = response.get_data(as_text=True)
        self.assertIn("style-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("style=", page)
        # 0% before training is a thin marker; 100% after training is a full-length bar.
        self.assertIn('<rect class="bar bar--pre" x="0" y="2" width="3"', page)
        self.assertIn('<rect class="bar bar--post" x="0" y="24" width="400"', page)
        self.assertEqual(page.count("<svg class=\"cue-row__bars\""), len(CUE_LABELS))

    def test_missed_cues_are_listed_as_focus_areas(self):
        response = self.finish_session([True] * 12, [False] + [True] * 11)
        missed = self.query(
            "SELECT s.cue FROM response r JOIN attempt a ON a.id = r.attempt_id "
            "JOIN scenario s ON s.id = r.scenario_id WHERE a.phase = 'post' AND r.is_correct = 0")
        self.assertEqual(len(missed), 1)
        next_steps = response.get_data(as_text=True).split(NEXT_STEPS)[1]
        for cue, label in CUE_LABELS.items():
            if cue == missed[0]["cue"]:
                self.assertIn(f"<li>{label}</li>", next_steps)
            else:
                self.assertNotIn(f"<li>{label}</li>", next_steps)

    def test_perfect_post_test_has_no_focus_areas(self):
        response = self.finish_session([True] * 6 + [False] * 6, [True] * 12)
        next_steps = response.get_data(as_text=True).split(NEXT_STEPS)[1]
        self.assertIn("You made no mistakes in the post-assessment", next_steps)
        self.assertNotIn("<li>", next_steps.split("</section>")[0])


class ChartGeometryTests(unittest.TestCase):
    def test_bar_widths_at_the_boundaries(self):
        self.assertEqual(results.bar_width(None), 0)     # cue not measured
        self.assertEqual(results.bar_width(0), 3)        # thin marker that stays visible
        self.assertEqual(results.bar_width(50), 200)
        self.assertEqual(results.bar_width(100), 400)
        self.assertIsNone(results.percent_correct(None))
        self.assertEqual(results.percent_correct(0.5), 50)
