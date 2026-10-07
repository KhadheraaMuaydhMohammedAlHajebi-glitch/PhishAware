"""M6 Usability Survey: the ten-item System Usability Scale (FR-09).

The survey is the final step and opens once the post-assessment is complete.
Every rating is checked against an allowlist before anything is stored, the
score comes from scoring.sus_score, and submitting twice changes nothing (NFR-05).
"""

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from src import repository
from src.modules.progress import phase_done
from src.modules.scoring import sus_score
from src.modules.security import require_consent

bp = Blueprint("survey", __name__, url_prefix="/survey")

# The standard SUS statements (Brooke, 1996). Odd items are worded positively
# and even items negatively, which sus_score takes into account.
SUS_ITEMS = (
    "I think that I would like to use this system frequently.",
    "I found the system unnecessarily complex.",
    "I thought the system was easy to use.",
    "I think that I would need the support of a technical person to be able to use this system.",
    "I found the various functions in this system were well integrated.",
    "I thought there was too much inconsistency in this system.",
    "I would imagine that most people would learn to use this system very quickly.",
    "I found the system very cumbersome to use.",
    "I felt very confident using the system.",
    "I needed to learn a lot of things before I could get going with this system.",
)
SCALE = (
    (1, "Strongly disagree"),
    (2, "Disagree"),
    (3, "Neutral"),
    (4, "Agree"),
    (5, "Strongly agree"),
)
ALLOWED_RATINGS = frozenset(str(value) for value, _ in SCALE)
ERROR_MESSAGE = "Choose one answer for every statement before you submit."


def parse_ratings(form):
    """Return the ten ratings as integers, or None if any is missing or off the scale."""
    ratings = []
    for number in range(1, len(SUS_ITEMS) + 1):
        value = form.get(f"q{number}", "")
        if value not in ALLOWED_RATINGS:
            return None
        ratings.append(int(value))
    return ratings


def _selected(form):
    """Valid answers already chosen, so a rejected form can be shown again as filled in."""
    return {
        number: int(form[f"q{number}"])
        for number in range(1, len(SUS_ITEMS) + 1)
        if form.get(f"q{number}") in ALLOWED_RATINGS
    }


def _survey_page(selected=None, error=None):
    return render_template(
        "survey.html", items=SUS_ITEMS, scale=SCALE, selected=selected or {}, error=error
    )


@bp.get("")
@require_consent
def survey_form():
    if not phase_done(g.participant, "post"):
        return redirect(url_for("consent.dashboard"))
    if repository.get_sus(g.participant["id"]) is not None:
        return redirect(url_for("survey.survey_complete"))
    return _survey_page()


@bp.post("")
@require_consent
def survey_submit():
    if not phase_done(g.participant, "post"):
        return redirect(url_for("consent.dashboard"))
    ratings = parse_ratings(request.form)
    if ratings is None:
        return _survey_page(_selected(request.form), ERROR_MESSAGE), 400
    if repository.save_sus(g.participant["id"], ratings, sus_score(ratings)):
        current_app.logger.info("Usability survey stored; participant marked as completed.")
    return redirect(url_for("survey.survey_complete"))  # Post/Redirect/Get pattern


@bp.get("/complete")
@require_consent
def survey_complete():
    if repository.get_sus(g.participant["id"]) is None:
        return redirect(url_for("survey.survey_form"))
    return render_template("survey_complete.html")
