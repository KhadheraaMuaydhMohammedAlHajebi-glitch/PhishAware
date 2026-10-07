"""White-box unit tests for the pure M5 scoring functions."""

import unittest

from src.modules import scoring


class ScoreAttemptTests(unittest.TestCase):
    def test_perfect_and_zero_scores(self):
        self.assertEqual(scoring.score_attempt(12, 12), 100.0)
        self.assertEqual(scoring.score_attempt(0, 12), 0.0)

    def test_rounds_to_one_decimal_place(self):
        self.assertEqual(scoring.score_attempt(7, 12), 58.3)

    def test_rejects_impossible_values(self):
        with self.assertRaises(ValueError):
            scoring.score_attempt(13, 12)
        with self.assertRaises(ValueError):
            scoring.score_attempt(1, 0)


class LearningGainTests(unittest.TestCase):
    def test_gain_in_percentage_points(self):
        self.assertEqual(scoring.learning_gain(58.3, 83.3), 25.0)

    def test_negative_gain_is_reported(self):
        self.assertEqual(scoring.learning_gain(75.0, 66.7), -8.3)


class SusScoreTests(unittest.TestCase):
    def test_best_possible_score(self):
        self.assertEqual(scoring.sus_score([5, 1] * 5), 100.0)

    def test_worst_possible_score(self):
        self.assertEqual(scoring.sus_score([1, 5] * 5), 0.0)

    def test_neutral_answers_score_fifty(self):
        self.assertEqual(scoring.sus_score([3] * 10), 50.0)

    def test_rejects_wrong_length_or_range(self):
        with self.assertRaises(ValueError):
            scoring.sus_score([3] * 9)
        with self.assertRaises(ValueError):
            scoring.sus_score([6] + [3] * 9)

    def test_rejects_boolean_ratings(self):
        # Boundary of the type check: bool is a subclass of int, and True == 1.
        with self.assertRaises(ValueError):
            scoring.sus_score([True] * 10)


class CounterbalancingTests(unittest.TestCase):
    def test_odd_sessions_start_with_form_a(self):
        self.assertEqual(scoring.assign_form_order(1), "AB")
        self.assertEqual(scoring.assign_form_order(3), "AB")

    def test_even_sessions_start_with_form_b(self):
        self.assertEqual(scoring.assign_form_order(2), "BA")

    def test_rejects_sequence_below_one(self):
        with self.assertRaises(ValueError):
            scoring.assign_form_order(0)


class CueErrorRateTests(unittest.TestCase):
    def test_error_rate_per_cue(self):
        rates = scoring.cue_error_rates([
            ("deceptive_links", True),
            ("deceptive_links", False),
            ("urgency_threats", True),
        ])
        self.assertEqual(rates, {"deceptive_links": 0.5, "urgency_threats": 0.0})


class DescribeTests(unittest.TestCase):
    def test_mean_median_and_sample_standard_deviation(self):
        # Gains of 25, 16.7, 25, 0, and 16.7 points: the median resists the zero.
        self.assertEqual(
            scoring.describe([25.0, 16.7, 25.0, 0.0, 16.7]),
            {"mean": 16.7, "median": 16.7, "sd": 10.2},
        )

    def test_even_count_takes_the_middle_of_the_two_central_values(self):
        self.assertEqual(scoring.describe([50.0, 75.0, 85.0, 100.0])["median"], 80.0)

    def test_single_value_has_no_standard_deviation(self):
        self.assertEqual(scoring.describe([75.0]), {"mean": 75.0, "median": 75.0, "sd": None})

    def test_accepts_a_generator_and_rejects_no_values(self):
        self.assertEqual(scoring.describe(value for value in (1, 2, 3))["mean"], 2.0)
        with self.assertRaises(ValueError):
            scoring.describe([])
