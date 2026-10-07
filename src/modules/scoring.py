"""M5 Scoring & Analytics: pure functions with no Flask or database access.

Pure functions are easy to unit-test and are reused by the assessment,
practice, and (in release 0.6) reporting modules.
"""

CUE_LABELS = {
    "sender_spoofing": "Sender spoofing",
    "deceptive_links": "Deceptive links",
    "urgency_threats": "Urgency or threats",
    "unexpected_attachments": "Unexpected attachments",
    "credential_requests": "Credential requests",
    "lookalike_websites": "Look-alike websites",
}
CUE_CATEGORIES = tuple(CUE_LABELS)


def score_attempt(correct, total=12):
    """Percentage of correct classifications, rounded to one decimal place."""
    if total <= 0:
        raise ValueError("total must be positive")
    if not 0 <= correct <= total:
        raise ValueError("correct must be between 0 and total")
    return round(correct / total * 100, 1)


def learning_gain(pre_score, post_score):
    """Post-test minus pre-test score, in percentage points (RQ1)."""
    return round(post_score - pre_score, 1)


def cue_error_rates(responses):
    """Error rate per cue from (cue, is_correct) pairs (RQ2)."""
    totals, errors = {}, {}
    for cue, is_correct in responses:
        totals[cue] = totals.get(cue, 0) + 1
        if not is_correct:
            errors[cue] = errors.get(cue, 0) + 1
    return {cue: round(errors.get(cue, 0) / count, 3) for cue, count in totals.items()}


def sus_score(ratings):
    """Standard System Usability Scale score (Brooke, 1996) from ten 1-5 ratings."""
    if len(ratings) != 10:
        raise ValueError("SUS requires exactly ten ratings")
    total = 0
    for number, rating in enumerate(ratings, start=1):
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValueError("each rating must be an integer from 1 to 5")
        total += (rating - 1) if number % 2 == 1 else (5 - rating)
    return total * 2.5


def assign_form_order(sequence):
    """Counterbalancing: odd sessions take Form A first, even sessions Form B."""
    if sequence < 1:
        raise ValueError("sequence numbers start at 1")
    return "AB" if sequence % 2 == 1 else "BA"


def validate_form(items, per_cue=2):
    """Return a list of balance problems for a parallel form (empty = balanced)."""
    problems = []
    expected = per_cue * len(CUE_CATEGORIES)
    if len(items) != expected:
        problems.append(f"expected {expected} items, found {len(items)}")
    for cue in CUE_CATEGORIES:
        count = sum(1 for item in items if item["cue"] == cue)
        if count != per_cue:
            problems.append(f"{cue}: expected {per_cue} items, found {count}")
    phishing = sum(1 for item in items if item["label"] == "phishing")
    if phishing * 2 != len(items):
        problems.append(f"labels unbalanced: {phishing} phishing of {len(items)}")
    return problems
