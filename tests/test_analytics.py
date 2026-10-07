"""White-box unit tests for the M5 analytics functions (FR-08).

Each test targets one branch of the code: missing cues, tied error rates,
the result limit, incomplete cases, and the zero-variance cohort.
"""

import unittest

from src.modules import scoring


class CueComparisonTests(unittest.TestCase):
    def test_reports_error_rates_and_change_per_cue(self):
        pre = [("deceptive_links", False), ("deceptive_links", True),
               ("urgency_threats", True), ("urgency_threats", True)]
        post = [("deceptive_links", True), ("deceptive_links", True),
                ("urgency_threats", False), ("urgency_threats", True)]
        rows = {row["cue"]: row for row in scoring.cue_comparison(pre, post)}
        self.assertEqual(
            rows["deceptive_links"],
            {"cue": "deceptive_links", "pre_error": 0.5, "post_error": 0.0, "change": -0.5},
        )
        self.assertEqual(rows["urgency_threats"]["change"], 0.5)
        self.assertEqual(len(rows), len(scoring.CUE_CATEGORIES))

    def test_cue_missing_from_one_phase_has_no_change(self):
        rows = {row["cue"]: row for row in scoring.cue_comparison(
            [("sender_spoofing", False)], [])}
        self.assertEqual(rows["sender_spoofing"]["pre_error"], 1.0)
        self.assertIsNone(rows["sender_spoofing"]["post_error"])
        self.assertIsNone(rows["sender_spoofing"]["change"])
        self.assertIsNone(rows["lookalike_websites"]["pre_error"])


class FocusAreaTests(unittest.TestCase):
    def test_no_mistakes_means_no_focus_areas(self):
        responses = [(cue, True) for cue in scoring.CUE_CATEGORIES]
        self.assertEqual(scoring.focus_areas(responses), [])

    def test_highest_error_rate_comes_first(self):
        responses = [
            ("sender_spoofing", False), ("sender_spoofing", True),      # 50% errors
            ("lookalike_websites", False), ("lookalike_websites", False),  # 100% errors
            ("deceptive_links", True), ("deceptive_links", True),       # no errors
        ]
        self.assertEqual(
            scoring.focus_areas(responses), ["lookalike_websites", "sender_spoofing"]
        )

    def test_ties_keep_lesson_order_and_limit_applies(self):
        responses = [
            ("credential_requests", False),
            ("urgency_threats", False),
            ("sender_spoofing", False),
        ]
        self.assertEqual(
            scoring.focus_areas(responses), ["sender_spoofing", "urgency_threats"]
        )
        self.assertEqual(scoring.focus_areas(responses, limit=1), ["sender_spoofing"])


class CohortSummaryTests(unittest.TestCase):
    def test_uses_complete_cases_and_paired_statistics(self):
        records = [
            {"pre": 50.0, "post": 70.0, "sus": 80.0},
            {"pre": 60.0, "post": 70.0, "sus": 70.0},
            {"pre": 75.0, "post": None, "sus": None},   # dropped: no post-test
            {"pre": None, "post": None, "sus": None},   # dropped: consent only
        ]
        self.assertEqual(scoring.cohort_summary(records), {
            "n": 2,
            "mean_pre": 55.0,
            "mean_post": 70.0,
            "mean_gain": 15.0,
            "sd_gain": 7.1,     # gains 20 and 10: SD = 10 / sqrt(2)
            "d_z": 2.12,        # 15 / 7.071
            "t": 3.0,           # 15 / (7.071 / sqrt(2))
            "mean_sus": 75.0,
        })

    def test_identical_gains_give_no_effect_size(self):
        records = [{"pre": 50.0, "post": 75.0}, {"pre": 25.0, "post": 50.0}]
        summary = scoring.cohort_summary(records)
        self.assertEqual(summary["sd_gain"], 0.0)
        self.assertIsNone(summary["d_z"])
        self.assertIsNone(summary["t"])
        self.assertIsNone(summary["mean_sus"])

    def test_requires_two_complete_participants(self):
        with self.assertRaises(ValueError):
            scoring.cohort_summary([{"pre": 50.0, "post": 75.0}, {"pre": 60.0, "post": None}])
