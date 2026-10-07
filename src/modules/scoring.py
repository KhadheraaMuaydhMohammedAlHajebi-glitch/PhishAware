"""M5 Scoring & Analytics: pure functions with no Flask or database access.

Pure functions are easy to unit-test and are reused by the assessment,
practice, results, and analytics modules.
"""

import math
import statistics

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


def cue_comparison(pre_responses, post_responses):
    """Per-cue error rates before and after training, with the change (RQ2).

    A cue missing from either phase has None for that phase and no change.
    """
    pre = cue_error_rates(pre_responses)
    post = cue_error_rates(post_responses)
    rows = []
    for cue in CUE_CATEGORIES:
        before, after = pre.get(cue), post.get(cue)
        change = None if before is None or after is None else round(after - before, 3)
        rows.append({"cue": cue, "pre_error": before, "post_error": after, "change": change})
    return rows


def focus_areas(responses, limit=2):
    """Cues the learner still misses, highest error rate first.

    Python's sort is stable, so ties keep the lesson order in CUE_CATEGORIES.
    """
    rates = cue_error_rates(responses)
    missed = [cue for cue in CUE_CATEGORIES if rates.get(cue, 0) > 0]
    return sorted(missed, key=lambda cue: rates[cue], reverse=True)[:limit]


def cohort_summary(records):
    """Cohort statistics for RQ1 and RQ3, using complete cases only.

    Each record holds "pre" and "post" percentages and an optional "sus" score.
    The effect size is Cohen's d_z for paired designs: mean gain / SD of gains.
    """
    complete = [r for r in records if r.get("pre") is not None and r.get("post") is not None]
    if len(complete) < 2:
        raise ValueError("at least two participants with both scores are required")
    gains = [r["post"] - r["pre"] for r in complete]
    mean_gain = statistics.fmean(gains)
    sd_gain = statistics.stdev(gains)
    sus = [r["sus"] for r in complete if r.get("sus") is not None]
    return {
        "n": len(complete),
        "mean_pre": round(statistics.fmean(r["pre"] for r in complete), 1),
        "mean_post": round(statistics.fmean(r["post"] for r in complete), 1),
        "mean_gain": round(mean_gain, 1),
        "sd_gain": round(sd_gain, 1),
        "d_z": round(mean_gain / sd_gain, 2) if sd_gain > 0 else None,
        "t": round(mean_gain / (sd_gain / math.sqrt(len(gains))), 2) if sd_gain > 0 else None,
        "mean_sus": round(statistics.fmean(sus), 1) if sus else None,
    }
