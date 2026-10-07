"""M2 Assessment Engine: counterbalanced pre- and post-assessment (FR-03, FR-07).

Odd-numbered sessions take Form A first and Form B last; even-numbered sessions
take the reverse. Every participant therefore answers both parallel forms, so a
difference in form difficulty cannot masquerade as a learning effect.

The engine is written once and parameterized by phase. "pre" and "post" share
every line of logic and differ only in the small configuration tables below, so
the post-test reuses the code path that the pre-test tests already verify.
"""

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from src import repository
from src.modules.progress import phase_done
from src.modules.scoring import score_attempt
from src.modules.security import require_consent, validate_answer, validate_scenario_id

bp = Blueprint("assessment", __name__, url_prefix="/assessment")

FORM_POSITION = {"pre": 0, "post": 1}  # index into the counterbalanced form order
PREREQUISITE = {"pre": None, "post": "practice"}
LABELS = {"pre": "Pre-assessment", "post": "Post-assessment"}
HINTS = {
    "pre": "Answer as you would in your own inbox. You will see explanations after the lessons.",
    "post": "Use what you practiced. Your results page compares both assessments afterwards.",
}


def form_for(participant, phase):
    """Form A or B for a phase, taken from the participant's counterbalanced order."""
    return participant["form_order"][FORM_POSITION[phase]]


def _is_open(participant, phase):
    """A phase opens once its prerequisite phase is complete (the pre-test has none)."""
    required = PREREQUISITE[phase]
    return required is None or phase_done(participant, required)


def _attempt(phase):
    """Get (or start) this participant's attempt for the phase, on the right form."""
    participant = g.participant
    return repository.get_or_create_attempt(
        participant["id"], phase, form_for(participant, phase)
    )


def _finalize(attempt, total, phase):
    """Score the attempt once every item is answered. True when complete."""
    if attempt["completed_at"] is not None:
        return True
    if len(repository.answered_ids(attempt["id"])) < total:
        return False
    score = score_attempt(repository.correct_count(attempt["id"]), total)
    repository.complete_attempt(attempt["id"], score)
    current_app.logger.info("%s completed and scored.", LABELS[phase])
    return True


def _show_item(phase):
    """Show the next unanswered item, or move on when the phase is complete."""
    if not _is_open(g.participant, phase):
        return redirect(url_for("consent.dashboard"))
    attempt = _attempt(phase)
    items = repository.scenarios_for_pool(attempt["form"])
    if _finalize(attempt, len(items), phase):
        return redirect(url_for(f"assessment.{phase}_complete"))
    answered = repository.answered_ids(attempt["id"])
    item = next(row for row in items if row["id"] not in answered)
    return render_template(
        "assessment_item.html",
        scenario=repository.scenario_content(item),
        position=len(answered) + 1,
        total=len(items),
        form=attempt["form"],
        label=LABELS[phase],
        hint=HINTS[phase],
        submit_url=url_for(f"assessment.{phase}_answer"),
    )


def _record_answer(phase):
    """Validate and store one answer; repeated submissions change nothing (NFR-05)."""
    if not _is_open(g.participant, phase):
        return redirect(url_for("consent.dashboard"))
    scenario_id = validate_scenario_id(request.form.get("scenario_id"))
    answer = validate_answer(request.form.get("answer"))
    attempt = _attempt(phase)
    if attempt["completed_at"] is None:
        scenario = repository.get_scenario(scenario_id, attempt["form"])
        if scenario is None:
            abort(400, description="That item is not part of your assessment form.")
        is_correct = answer == scenario["label"]
        repository.record_response(attempt["id"], scenario_id, answer, is_correct)
        _finalize(attempt, len(repository.scenarios_for_pool(attempt["form"])), phase)
    return redirect(url_for(f"assessment.{phase}_item"))  # Post/Redirect/Get pattern


def _show_complete(phase):
    """Show the stored score once the phase is complete."""
    attempt = repository.get_attempt(g.participant["id"], phase)
    if attempt is None or attempt["completed_at"] is None:
        return redirect(url_for(f"assessment.{phase}_item"))
    return render_template(
        "assessment_complete.html",
        phase=phase,
        label=LABELS[phase],
        score=attempt["score"],
        correct=repository.correct_count(attempt["id"]),
        total=len(repository.scenarios_for_pool(attempt["form"])),
        form=attempt["form"],
    )


# Routes: thin wrappers, so Flask endpoint names stay stable for url_for().
@bp.get("/pre")
@require_consent
def pre_item():
    return _show_item("pre")


@bp.post("/pre")
@require_consent
def pre_answer():
    return _record_answer("pre")


@bp.get("/pre/complete")
@require_consent
def pre_complete():
    return _show_complete("pre")


@bp.get("/post")
@require_consent
def post_item():
    return _show_item("post")


@bp.post("/post")
@require_consent
def post_answer():
    return _record_answer("post")


@bp.get("/post/complete")
@require_consent
def post_complete():
    return _show_complete("post")
