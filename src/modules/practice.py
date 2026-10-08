"""M4 Scenario Practice & Feedback (FR-05, FR-06).

Participants classify fictional emails and web pages from a separate practice
pool and receive an immediate, cue-level explanation after every decision.
Practice items never appear in the assessment forms, so post-test scores are
not inflated by prior exposure (Unit 3 design, Section 3.2.2).
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from src import repository
from src.modules.progress import lessons_done, pretest_done
from src.modules.scoring import CUE_LABELS, score_attempt
from src.modules.security import require_consent, validate_answer, validate_scenario_id

bp = Blueprint("practice", __name__, url_prefix="/practice")
PRACTICE_POOL = "P"


def _not_yet_open():
    """A redirect to the step that must come first, or None when practice is open."""
    if not pretest_done(g.participant):
        return redirect(url_for("assessment.pre_item"))
    if not lessons_done(g.participant):
        return redirect(url_for("learning.lessons"))
    return None


def _attempt():
    return repository.get_or_create_attempt(g.participant["id"], "practice", PRACTICE_POOL)


def _complete_if_finished(attempt, total):
    """Store the practice score once every practice scenario is answered."""
    answered = len(repository.answered_ids(attempt["id"]))
    if attempt["completed_at"] is None and answered >= total:
        score = score_attempt(repository.correct_count(attempt["id"]), total)
        repository.complete_attempt(attempt["id"], score)


@bp.get("")
@require_consent
def practice_item():
    earlier_step = _not_yet_open()
    if earlier_step is not None:
        return earlier_step
    attempt = _attempt()
    items = repository.scenarios_for_pool(PRACTICE_POOL)
    answered = repository.answered_ids(attempt["id"])
    remaining = [row for row in items if row["id"] not in answered]
    if not remaining:
        _complete_if_finished(attempt, len(items))
        return redirect(url_for("practice.practice_complete"))
    return render_template(
        "practice_item.html",
        scenario=repository.scenario_content(remaining[0]),
        position=len(answered) + 1,
        total=len(items),
        submit_url=url_for("practice.practice_answer"),
    )


@bp.post("")
@require_consent
def practice_answer():
    earlier_step = _not_yet_open()
    if earlier_step is not None:
        return earlier_step
    scenario_id = validate_scenario_id(request.form.get("scenario_id"))
    answer = validate_answer(request.form.get("answer"))
    scenario = repository.get_scenario(scenario_id, PRACTICE_POOL)
    if scenario is None:
        abort(400, description="That scenario is not part of the practice set.")
    attempt = _attempt()
    repository.record_response(attempt["id"], scenario_id, answer, answer == scenario["label"])
    _complete_if_finished(attempt, len(repository.scenarios_for_pool(PRACTICE_POOL)))
    return redirect(url_for("practice.feedback", scenario_id=scenario_id))


@bp.get("/feedback/<scenario_id>")
@require_consent
def feedback(scenario_id):
    validate_scenario_id(scenario_id)
    attempt = repository.get_attempt(g.participant["id"], "practice")
    scenario = repository.get_scenario(scenario_id, PRACTICE_POOL)
    response = None
    if attempt is not None and scenario is not None:
        response = repository.get_response(attempt["id"], scenario_id)
    if response is None:
        # Feedback is revealed only for scenarios this participant has answered.
        abort(404)
    return render_template(
        "practice_feedback.html",
        scenario=repository.scenario_content(scenario),
        answer=response["answer"],
        correct=bool(response["is_correct"]),
        cue_label=CUE_LABELS.get(scenario["cue"], scenario["cue"]),
        answered=len(repository.answered_ids(attempt["id"])),
        total=len(repository.scenarios_for_pool(PRACTICE_POOL)),
        reveal=True,
    )


@bp.get("/complete")
@require_consent
def practice_complete():
    attempt = repository.get_attempt(g.participant["id"], "practice")
    total = len(repository.scenarios_for_pool(PRACTICE_POOL))
    if attempt is None or len(repository.answered_ids(attempt["id"])) < total:
        return redirect(url_for("practice.practice_item"))
    results = [
        {"cue": CUE_LABELS.get(cue, cue), "correct": is_correct}
        for cue, is_correct in repository.responses_with_cues(attempt["id"])
    ]
    return render_template(
        "practice_complete.html",
        correct=repository.correct_count(attempt["id"]),
        total=total,
        results=results,
    )
