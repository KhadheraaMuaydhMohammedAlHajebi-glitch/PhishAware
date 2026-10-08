"""M5 Results: the participant's personal before-and-after comparison (FR-08).

The page answers three questions for the learner: did my score change (RQ1),
which cues improved (RQ2), and what should I practise next. Every number comes
from the pure functions in scoring.py; this module only gathers the stored
responses and prepares the chart geometry.
"""

from flask import Blueprint, g, redirect, render_template, url_for

from src import repository
from src.modules.progress import phase_done
from src.modules.scoring import CUE_LABELS, cue_comparison, focus_areas, learning_gain
from src.modules.security import require_consent

bp = Blueprint("results", __name__)

BAR_FULL_WIDTH = 400   # SVG user units for a bar that represents 100%
BAR_MIN_WIDTH = 3      # keeps a 0% bar visible as a thin marker
CHART_LABEL_SPACE = 72  # room to the right of a full bar for its "100%" label


def percent_correct(error_rate):
    """Convert an error rate (0-1) into a whole-number percentage of correct answers."""
    return None if error_rate is None else round((1 - error_rate) * 100)


def bar_width(percent):
    """Bar length in SVG user units for a percentage."""
    if percent is None:
        return 0
    return max(BAR_MIN_WIDTH, round(BAR_FULL_WIDTH * percent / 100))


def chart_rows(comparison):
    """Turn per-cue error rates into bar geometry for the server-rendered chart.

    The Content-Security-Policy forbids inline styles (style-src 'self'), so bar
    lengths cannot be set with a style attribute. SVG width attributes are plain
    markup rather than styles, which keeps the policy intact (NFR-10).
    """
    rows = []
    for row in comparison:
        before = percent_correct(row["pre_error"])
        after = percent_correct(row["post_error"])
        rows.append({
            "label": CUE_LABELS[row["cue"]],
            "pre": before,
            "post": after,
            "pre_width": bar_width(before),
            "post_width": bar_width(after),
        })
    return rows


@bp.get("/results")
@require_consent
def results():
    participant = g.participant
    if not phase_done(participant, "post"):
        return redirect(url_for("consent.dashboard"))
    pre = repository.get_attempt(participant["id"], "pre")
    post = repository.get_attempt(participant["id"], "post")
    pre_responses = repository.responses_with_cues(pre["id"])
    post_responses = repository.responses_with_cues(post["id"])
    return render_template(
        "results.html",
        pre_score=pre["score"],
        post_score=post["score"],
        gain=learning_gain(pre["score"], post["score"]),
        rows=chart_rows(cue_comparison(pre_responses, post_responses)),
        focus=[CUE_LABELS[cue] for cue in focus_areas(post_responses)],
        chart_width=BAR_FULL_WIDTH + CHART_LABEL_SPACE,
        survey_done=repository.get_sus(participant["id"]) is not None,
    )
