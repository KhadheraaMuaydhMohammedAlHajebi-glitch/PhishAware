"""M5 Analytics: cohort-level answers to the three research questions (FR-08).

    flask --app src.app analytics

The command prints aggregate statistics only. No query in this module selects
a participant identifier, so the output can be shared without revealing who
took part (NFR-11). All statistics come from the pure functions in scoring.py.
"""

import click
from flask.cli import with_appcontext

from src import repository
from src.modules.scoring import CUE_LABELS, cohort_summary, cue_comparison

MINIMUM_COMPLETE = 2  # a standard deviation of gains needs at least two participants


def build_report():
    """Gather the stored records and compute the cohort statistics."""
    records = repository.cohort_records()
    complete = [r for r in records if r["pre"] is not None and r["post"] is not None]
    report = {"consented": repository.participant_count(), "summary": None, "cues": []}
    if len(complete) >= MINIMUM_COMPLETE:
        report["summary"] = cohort_summary(records)
        report["cues"] = cue_comparison(
            repository.cohort_responses("pre"), repository.cohort_responses("post")
        )
    return report


def _percent(rate):
    return "n/a" if rate is None else f"{round(rate * 100)}%"


def format_report(report):
    """Render a report as plain-text lines, one research question per block."""
    lines = [f"Participants who consented: {report['consented']}"]
    summary = report["summary"]
    if summary is None:
        lines.append("RQ1  Not enough data yet: two participants must finish both assessments.")
        return lines
    lines.append(
        f"RQ1  n = {summary['n']}: mean pre {summary['mean_pre']:.1f}%, "
        f"mean post {summary['mean_post']:.1f}%, mean gain {summary['mean_gain']:+.1f} points"
    )
    if summary["d_z"] is None:
        lines.append(f"     SD of gains {summary['sd_gain']:.1f}: identical gains, no effect size")
    else:
        lines.append(
            f"     SD of gains {summary['sd_gain']:.1f}, d_z = {summary['d_z']:.2f}, "
            f"t({summary['n'] - 1}) = {summary['t']:.2f}"
        )
    lines.append("RQ2  Error rate by cue (pre -> post)")
    for row in report["cues"]:
        label = CUE_LABELS[row["cue"]]
        lines.append(
            f"     {label:<24}{_percent(row['pre_error']):>5} -> {_percent(row['post_error']):>5}"
        )
    sus = summary["mean_sus"]
    sus_text = "no survey responses yet" if sus is None else f"{sus:.1f}"
    lines.append(f"RQ3  Mean SUS score: {sus_text}")
    return lines


@click.command("analytics")
@with_appcontext
def analytics_command():
    """Print cohort statistics for the three research questions (no identifiers)."""
    for line in format_report(build_report()):
        click.echo(line)
