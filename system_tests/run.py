"""Run the integration, system, and acceptance cases and print one test report.

    python -m system_tests.run --level integration
    python -m system_tests.run --base-url https://localhost --cacert caddy-root.crt \\
        --admin-user evaluator --admin-password "$PASSWORD" \\
        --cli "docker compose exec -T app flask"

Integration cases run inside this process and need no server. System and
acceptance cases need a running instance; without --base-url they are reported
as "not run". Every case names itself in its docstring, which holds nothing else
and may continue on a second line:

    ST-05 | NFR-05 | A double click stores one record
    AT-01 | FR-01 | critical | Nothing is stored before consent

The report lists each case with its verdict, counts the verdicts by level, and
states how many critical acceptance cases passed, which is the measure of
objective O2. The exit status is 1 when a case failed, and with --strict also
when a case could not be run.
"""

import argparse
import json
import os
import platform
import sys
import time
import traceback
import unittest
from pathlib import Path

LEVELS = {
    "integration": ("Integration", "tests.test_integration"),
    "system": ("System", "system_tests.test_system"),
    "acceptance": ("Acceptance", "system_tests.test_acceptance"),
}
VERDICTS = ("PASS", "FAIL", "ERROR", "NOT RUN")
MAX_SUBTESTS_SHOWN, MAX_LINES, MAX_LINE = 4, 6, 300
OPTIONS = {   # command-line option -> environment variable read by harness.Settings
    "base_url": "PHISHAWARE_BASE_URL", "cacert": "PHISHAWARE_CACERT",
    "admin_user": "PHISHAWARE_ADMIN_USER", "admin_password": "PHISHAWARE_ADMIN_PASSWORD",
    "browser": "PHISHAWARE_BROWSER", "cli": "PHISHAWARE_CLI",
    "crash_command": "PHISHAWARE_CRASH_COMMAND", "screenshots": "PHISHAWARE_ARTIFACTS",
}


def describe(test):
    """(case ID, requirement, critical?, title) from a test's docstring.

    The docstring may run over several lines; they are read as one.
    """
    parts = [part.strip() for part in " ".join((test._testMethodDoc or "").split()).split("|")]
    if len(parts) < 3:
        return test._testMethodName, "", False, test._testMethodName
    critical = "critical" in parts[2:-1]
    return parts[0], parts[1], critical, parts[-1].rstrip(".")


class Recorder(unittest.TestResult):
    """Collects one record per case and prints it as soon as the case has finished."""

    def __init__(self, level, stream):
        super().__init__()
        self.level = level
        self.stream = stream
        self.records = []
        self._started = None
        self._partial = None    # failures of sub-tests, reported with their case

    def startTest(self, test):
        super().startTest(test)
        self._started = time.perf_counter()
        self._partial = []
        self._recorded = False

    def stopTest(self, test):
        # A case whose only failures are in sub-tests gets no other callback.
        if not self._recorded and self._partial:
            self._record(test, "FAIL")
        super().stopTest(test)

    def _record(self, test, verdict, detail=""):
        case, requirement, critical, title = describe(test)
        seconds = time.perf_counter() - self._started if self._started else 0.0
        if self._partial and verdict in ("FAIL", "ERROR"):
            # Sub-tests that failed: the first few in full, the rest as a count.
            shown = self._partial[:MAX_SUBTESTS_SHOWN]
            if len(self._partial) > len(shown):
                shown.append(f"... and {len(self._partial) - len(shown)} more sub-test(s) failed")
            detail = "\n".join([*shown, detail]).strip()
        record = {"level": self.level, "id": case, "requirement": requirement,
                  "critical": critical, "title": title, "verdict": verdict,
                  "seconds": round(seconds, 1), "detail": detail,
                  # What a case measured, when it sets "self.note".
                  "note": getattr(test, "note", "") if verdict == "PASS" else ""}
        self.records.append(record)
        self._recorded = True
        self.stream.write(format_case(record) + "\n")
        if record["note"]:
            self.stream.write(f"{'':<25}{record['note']}\n")
        self.stream.flush()

    @staticmethod
    def _reason(error):
        """The message of a failure, without the traceback, cut to a readable length."""
        text = "".join(traceback.format_exception_only(error[0], error[1])).strip()
        lines = [line[:MAX_LINE] + (" ..." if len(line) > MAX_LINE else "")
                 for line in text.splitlines()]
        return "\n".join(lines[:MAX_LINES])

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "FAIL" if self._partial else "PASS")

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self._record(test, "FAIL", self._reason(error))

    def addError(self, test, error):
        super().addError(test, error)
        if isinstance(test, unittest.TestCase) and hasattr(test, "_testMethodName"):
            self._record(test, "ERROR", self._reason(error))
        else:   # a failure outside a case, such as an import error or a class fixture
            self._started = None
            self.records.append({
                "level": self.level, "id": "-", "requirement": "", "critical": False,
                "title": str(test), "verdict": "ERROR", "seconds": 0.0,
                "detail": self._reason(error), "note": ""})
            self.stream.write(f"  -       ERROR   {test}\n")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._started = self._started or time.perf_counter()
        self._record(test, "NOT RUN", reason)

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            reason = self._reason(error).splitlines()[0]
            self._partial.append(f"{subtest._subDescription()}: {reason}")


def format_case(record):
    mark = " *" if record["critical"] else "  "
    return (f"  {record['id']:<7}{record['verdict']:<8}{record['seconds']:>6.1f} s  "
            f"{record['requirement']:<15}{record['title']}{mark}")


def summarise(records):
    """Verdict counts for each level, in the order the levels ran."""
    table = {}
    for record in records:
        row = table.setdefault(record["level"], dict.fromkeys(VERDICTS, 0))
        row[record["verdict"]] += 1
    return table


def format_summary(report):
    lines = ["", "Summary",
             f"  {'Level':<14}{'Cases':>6}{'Passed':>8}{'Failed':>8}{'Errors':>8}{'Not run':>9}"]
    total = dict.fromkeys(VERDICTS, 0)
    for level, row in report["summary"].items():
        lines.append(f"  {level:<14}{sum(row.values()):>6}{row['PASS']:>8}{row['FAIL']:>8}"
                     f"{row['ERROR']:>8}{row['NOT RUN']:>9}")
        for verdict in VERDICTS:
            total[verdict] += row[verdict]
    lines.append(f"  {'Total':<14}{sum(total.values()):>6}{total['PASS']:>8}{total['FAIL']:>8}"
                 f"{total['ERROR']:>8}{total['NOT RUN']:>9}")
    critical = report["critical"]
    if critical["cases"]:
        share = critical["passed"] / critical["cases"] * 100
        lines.append(f"  Critical acceptance cases (*): {critical['passed']} of "
                     f"{critical['cases']} passed ({share:.0f}%; objective O2 requires 100%)")
    problems = [r for r in report["cases"] if r["verdict"] in ("FAIL", "ERROR")]
    if problems:
        lines += ["", "Cases that did not pass"]
        for record in problems:
            lines.append(f"  {record['id']} {record['verdict']}: {record['title']}")
            lines += [f"      {line}" for line in record["detail"].splitlines()]
    skipped = [r for r in report["cases"] if r["verdict"] == "NOT RUN"]
    if skipped:
        lines += ["", "Cases that were not run"]
        lines += [f"  {record['id']}: {record['detail']}" for record in skipped]
    lines += ["", f"Result: {report['result']}"]
    return lines


def format_markdown(report):
    """The same report as Markdown, for a CI run summary or a document."""
    lines = [f"### Test run: {report['target']}", "",
             f"{report['environment']}", "",
             "| Level | Cases | Passed | Failed | Errors | Not run |", "|---|---|---|---|---|---|"]
    for level, row in report["summary"].items():
        lines.append(f"| {level} | {sum(row.values())} | {row['PASS']} | {row['FAIL']} | "
                     f"{row['ERROR']} | {row['NOT RUN']} |")
    critical = report["critical"]
    if critical["cases"]:
        lines += ["", f"Critical acceptance cases: **{critical['passed']} of "
                  f"{critical['cases']}** passed."]
    lines += ["", "| Case | Requirement | Verdict | Seconds | Title |", "|---|---|---|---|---|"]
    for record in report["cases"]:
        lines.append(f"| {record['id']}{' *' if record['critical'] else ''} | "
                     f"{record['requirement']} | {record['verdict']} | {record['seconds']} | "
                     f"{record['title']} |")
    return lines + ["", f"**{report['result']}**", ""]


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--level", default="all",
                        help="integration, system, acceptance, a comma-separated list, or all")
    parser.add_argument("--base-url", help="the instance under test, for example https://localhost")
    parser.add_argument("--cacert", help="extra certificate authority to trust (PEM file)")
    parser.add_argument("--ignore-https-errors", action="store_true",
                        help="let the browser accept a certificate from a local authority")
    parser.add_argument("--admin-user")
    parser.add_argument("--admin-password")
    parser.add_argument("--browser", help="chromium (default), firefox, or webkit")
    parser.add_argument("--cli", help='how to run "flask" on the instance, for operator cases')
    parser.add_argument("--crash-command",
                        help="a command that kills the web service without warning")
    parser.add_argument("--screenshots", help="folder for screenshots taken as evidence")
    parser.add_argument("--only", help="run only the cases whose ID starts with this text")
    parser.add_argument("--strict", action="store_true",
                        help="count a case that could not be run as a failure")
    parser.add_argument("--json", help="write the full result to this file")
    parser.add_argument("--markdown", help="append the report as Markdown to this file")
    return parser.parse_args(argv)


def cases_of(suite):
    """Every case of a suite, however deeply the loader nested it."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases_of(item)
        else:
            yield item


def in_order(suite, prefix=None):
    """The cases in the order of their IDs (IT-01, IT-02, ...), optionally only some of them."""
    def key(case):
        name, _, number = describe(case)[0].rpartition("-")
        return name, int(number) if number.isdigit() else 0, case.id()

    chosen = [case for case in cases_of(suite)
              if prefix is None or describe(case)[0].startswith(prefix)]
    return unittest.TestSuite(sorted(chosen, key=key))


def environment(levels):
    """One line that says what ran the cases, so that a report can be read on its own."""
    parts = [f"Python {platform.python_version()}"]
    if any(level != "integration" for level in levels) and os.environ.get("PHISHAWARE_BASE_URL"):
        from system_tests import harness
        try:
            reply = harness.Client(timeout=10).get("/healthz")
            parts.insert(0, f"PhishAware {json.loads(reply.text).get('version', 'unknown')}")
        except Exception as error:   # the cases themselves will report an unreachable instance
            parts.insert(0, f"instance not reachable ({type(error).__name__})")
        if any(level in levels for level in ("system", "acceptance")):
            try:
                parts.append(harness.browser_version())
            except Exception as error:
                parts.append(f"no browser ({type(error).__name__})")
    return ", ".join(parts)


def main(argv=None):
    args = parse_arguments(argv)
    for option, variable in OPTIONS.items():
        value = getattr(args, option)
        if value:
            os.environ[variable] = value
    if args.ignore_https_errors:
        os.environ["PHISHAWARE_IGNORE_HTTPS_ERRORS"] = "1"
    names = list(LEVELS) if args.level == "all" else [n.strip() for n in args.level.split(",")]
    unknown = [name for name in names if name not in LEVELS]
    if unknown:
        raise SystemExit(f"unknown level: {', '.join(unknown)}")
    if os.environ.get("PHISHAWARE_BASE_URL") is None and "integration" not in names:
        print("No --base-url was given, so the cases are listed as not run.")

    target = os.environ.get("PHISHAWARE_BASE_URL") or "this process (no server)"
    described = environment(names)
    print(f"PhishAware test run: {target}")
    print(f"  {described}")
    records = []
    started = time.perf_counter()
    for name in names:
        label, module = LEVELS[name]
        print(f"\n{label} cases ({module})")
        suite = in_order(unittest.defaultTestLoader.loadTestsFromName(module), args.only)
        recorder = Recorder(label, sys.stdout)
        suite.run(recorder)
        records += recorder.records

    critical = [r for r in records if r["critical"]]
    failed = sum(1 for r in records if r["verdict"] in ("FAIL", "ERROR"))
    not_run = sum(1 for r in records if r["verdict"] == "NOT RUN")
    passed = sum(1 for r in records if r["verdict"] == "PASS")
    if failed:
        result = f"{failed} of {len(records)} cases did not pass"
    elif not_run:
        result = f"{passed} cases passed, {not_run} not run"
    else:
        result = f"all {passed} cases passed"
    report = {
        "target": target,
        "environment": described,
        "seconds": round(time.perf_counter() - started, 1),
        "summary": summarise(records),
        "critical": {"cases": len(critical),
                     "passed": sum(1 for r in critical if r["verdict"] == "PASS")},
        "cases": records,
        "result": result,
    }
    for line in format_summary(report):
        print(line)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as handle:
            handle.write("\n".join(format_markdown(report)) + "\n")
    return 1 if failed or (args.strict and not_run) else 0


if __name__ == "__main__":
    sys.exit(main())
