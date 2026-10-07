"""Run the core PhishAware algorithms on small inputs that can be checked by hand.

    python scripts/demo_core_logic.py

The algorithms are pure functions, so this script needs no server and no database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.modules import scoring  # noqa: E402  (the project root must be on the path first)

CUES = scoring.CUE_CATEGORIES


def responses(errors):
    """Two items per cue; errors maps a cue to its number of wrong answers."""
    pairs = []
    for cue in CUES:
        wrong = errors.get(cue, 0)
        pairs += [(cue, False)] * wrong + [(cue, True)] * (2 - wrong)
    return pairs


def main():
    orders = ", ".join(scoring.assign_form_order(n) for n in range(1, 5))
    print(f"1. Counterbalancing, sessions 1-4: {orders}")

    pre = responses({"deceptive_links": 1, "lookalike_websites": 2})
    post = responses({"lookalike_websites": 1})
    pre_score = scoring.score_attempt(sum(ok for _, ok in pre), len(pre))
    post_score = scoring.score_attempt(sum(ok for _, ok in post), len(post))
    gain = scoring.learning_gain(pre_score, post_score)
    print(f"2. Scores: pre {sum(ok for _, ok in pre)}/12 = {pre_score}%, "
          f"post {sum(ok for _, ok in post)}/12 = {post_score}%, gain = {gain:+} points")

    print("3. Error rate by cue (pre -> post):")
    for row in scoring.cue_comparison(pre, post):
        label = scoring.CUE_LABELS[row["cue"]]
        print(f"   {label:<24}{row['pre_error']:.2f} -> {row['post_error']:.2f}")

    focus = [scoring.CUE_LABELS[cue] for cue in scoring.focus_areas(post)]
    print(f"4. Focus areas after training: {focus}")

    ratings = [4, 2, 5, 1, 4, 2, 5, 2, 4, 1]
    print(f"5. SUS score for {ratings}: {scoring.sus_score(ratings)}")

    sample = [
        {"pre": 50.0, "post": 70.0, "sus": 80.0},
        {"pre": 60.0, "post": 70.0, "sus": 70.0},
        {"pre": 70.0, "post": 85.0, "sus": 85.0},
        {"pre": 40.0, "post": 60.0, "sus": 75.0},
        {"pre": 55.0, "post": 65.0, "sus": 72.5},
    ]
    print("6. Cohort summary (five sample records):")
    for key, value in scoring.cohort_summary(sample).items():
        print(f"   {key:<10}{value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
