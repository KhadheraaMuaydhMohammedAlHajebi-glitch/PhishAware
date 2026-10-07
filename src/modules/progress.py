"""Participant progress shared by the dashboard and the module guards."""

from src import repository


def phase_status(participant_id, phase, total):
    """Summarise one phase (pre, practice, post) for the dashboard."""
    attempt = repository.get_attempt(participant_id, phase)
    if attempt is None:
        return {
            "started": False,
            "done": False,
            "answered": 0,
            "total": total,
            "correct": 0,
            "score": None,
        }
    return {
        "started": True,
        "done": attempt["completed_at"] is not None,
        "answered": len(repository.answered_ids(attempt["id"])),
        "total": total,
        "correct": repository.correct_count(attempt["id"]),
        "score": attempt["score"],
    }


def build_progress(participant):
    """Everything the dashboard needs, computed from pseudonymous records only."""
    participant_id = participant["id"]
    order = participant["form_order"]
    pre_total = len(repository.scenarios_for_pool(order[0]))
    practice_total = len(repository.scenarios_for_pool("P"))
    post_total = len(repository.scenarios_for_pool(order[1]))
    return {
        "short_id": participant_id[-6:],
        "form_order": order,
        "pre": phase_status(participant_id, "pre", pre_total),
        "practice": phase_status(participant_id, "practice", practice_total),
        "post": phase_status(participant_id, "post", post_total),
        "survey_done": repository.get_sus(participant_id) is not None,
    }


def phase_done(participant, phase):
    """True once the participant has finished the given phase."""
    attempt = repository.get_attempt(participant["id"], phase)
    return attempt is not None and attempt["completed_at"] is not None


def pretest_done(participant):
    """True once the participant has finished the pre-assessment."""
    return phase_done(participant, "pre")
