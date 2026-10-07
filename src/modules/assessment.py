"""M2 Assessment Engine: counterbalanced 12-item pre-assessment (FR-03).

Odd-numbered sessions receive Form A as the pre-test and even-numbered sessions
receive Form B, so a difference in form difficulty cannot masquerade as a
learning effect. The post-test (FR-07) will reuse this engine in release 0.5.
"""

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from src import repository
from src.modules.scoring import score_attempt
from src.modules.security import require_consent, validate_answer, validate_scenario_id

bp = Blueprint("assessment", __name__, url_prefix="/assessment")


def _pre_attempt():
    """Get (or start) the pre-test attempt on the participant's first form."""
    participant = g.participant
    first_form = participant["form_order"][0]
    return repository.get_or_create_attempt(participant["id"], "pre", first_form)


def _finalize(attempt, total):
    """Score the attempt once every item is answered. True when complete."""
    if attempt["completed_at"] is not None:
        return True
    if len(repository.answered_ids(attempt["id"])) < total:
        return False
    score = score_attempt(repository.correct_count(attempt["id"]), total)
    repository.complete_attempt(attempt["id"], score)
    current_app.logger.info("Pre-assessment completed and scored.")
    return True


@bp.get("/pre")
@require_consent
def pre_item():
    attempt = _pre_attempt()
    items = repository.scenarios_for_pool(attempt["form"])
    if _finalize(attempt, len(items)):
        return redirect(url_for("assessment.pre_complete"))
    answered = repository.answered_ids(attempt["id"])
    item = next(row for row in items if row["id"] not in answered)
    return render_template(
        "assessment_item.html",
        scenario=repository.scenario_content(item),
        position=len(answered) + 1,
        total=len(items),
        form=attempt["form"],
        submit_url=url_for("assessment.pre_answer"),
    )


@bp.post("/pre")
@require_consent
def pre_answer():
    scenario_id = validate_scenario_id(request.form.get("scenario_id"))
    answer = validate_answer(request.form.get("answer"))
    attempt = _pre_attempt()
    if attempt["completed_at"] is None:
        scenario = repository.get_scenario(scenario_id, attempt["form"])
        if scenario is None:
            abort(400, description="That item is not part of your assessment form.")
        is_correct = answer == scenario["label"]
        repository.record_response(attempt["id"], scenario_id, answer, is_correct)
        _finalize(attempt, len(repository.scenarios_for_pool(attempt["form"])))
    return redirect(url_for("assessment.pre_item"))  # Post/Redirect/Get pattern


@bp.get("/pre/complete")
@require_consent
def pre_complete():
    attempt = repository.get_attempt(g.participant["id"], "pre")
    if attempt is None or attempt["completed_at"] is None:
        return redirect(url_for("assessment.pre_item"))
    return render_template(
        "assessment_complete.html",
        score=attempt["score"],
        correct=repository.correct_count(attempt["id"]),
        total=len(repository.scenarios_for_pool(attempt["form"])),
        form=attempt["form"],
    )
