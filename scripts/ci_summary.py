"""Publish CI results where a reviewer looks first: the run summary and annotations.

    python scripts/ci_summary.py quality            after the lint, test, and scan steps
    python scripts/ci_summary.py failure FILE ...   after a failed step: show each log's end

GitHub shows annotations beside the job and in the Checks API, so a failure can
be diagnosed without opening the raw log. The script uses the standard library only.
"""

import os
import re
import sys
from pathlib import Path

TAIL_LINES = 40
MAX_CHARS = 3000


def escape(text):
    """Encode a message for a GitHub workflow command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(level, title, message):
    title = escape(title).replace(":", "%3A").replace(",", "%2C")
    print(f"::{level} title={title}::{escape(message)}")


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def last_match(pattern, text, default="not available"):
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    return matches[-1].strip() if matches else default


def quality():
    """Summarise the test, coverage, and security-scan logs."""
    rows = [
        ("Tests", last_match(r"^=*\s*(\d+ passed.*?)\s*=*$", read("pytest.log"))),
        ("Statement coverage", last_match(r"^TOTAL\s+(.*)$", read("coverage.txt"))),
        ("Bandit", last_match(r"^\s*(No issues identified\.|Total issues.*)$", read("bandit.txt"))),
        ("pip-audit", last_match(r"^(No known vulnerabilities found.*|Found \d+ known.*)$",
                                 read("audit.txt"))),
    ]
    lines = ["| Check | Result |", "|---|---|"] + [f"| {name} | {value} |" for name, value in rows]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    for name, value in rows:
        annotate("notice", name, value)
    return 0


def failure(paths):
    """Surface the last lines of each log as an error annotation."""
    for path in paths:
        text = read(path).strip()
        if not text:
            continue
        tail = "\n".join(text.splitlines()[-TAIL_LINES:])[-MAX_CHARS:]
        annotate("error", f"End of {path}", tail)
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "quality":
        return quality()
    if len(argv) >= 3 and argv[1] == "failure":
        return failure(argv[2:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
