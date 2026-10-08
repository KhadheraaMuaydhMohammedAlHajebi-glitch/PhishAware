"""Load, latency, and result-accuracy test for a running PhishAware instance.

    python evaluation/loadtest.py --base-url http://127.0.0.1:5000 --levels 1,5,25 \\
        --admin-user evaluator --admin-password "$PASSWORD"

Every simulated participant completes the whole journey over real HTTP: consent,
the 12-item pre-assessment, the lessons, six practice scenarios with feedback,
the 12-item post-assessment, the results page, the usability survey, and the
Finish step that ends the session.

The script measures three things:

* Latency. "submit" is the time to POST one answer, "page" the time to GET one
  screen (redirects included), "turnaround" the time from sending an answer to
  receiving the next screen, and "static" the time to GET one static file.
* Throughput. HTTP requests per second and completed journeys per minute.
* Result accuracy. The answers are scripted from a fixed seed, so every score
  the system reports has a known correct value. The script works that value out
  with its own code (an independent oracle) and compares it with the results
  page, the administrator's dashboard, and the de-identified export.

The scripted answers are test data, not findings about learners. Start from an
empty database so that the dashboard and export checks see only this run. The
script uses the standard library only.
"""

import argparse
import csv
import http.client
import io
import json
import math
import random
import re
import ssl
import statistics
import sys
import threading
import time
import urllib.parse
from pathlib import Path

CUES = (
    "sender_spoofing", "deceptive_links", "urgency_threats",
    "unexpected_attachments", "credential_requests", "lookalike_websites",
)
CUE_LABELS = {
    "sender_spoofing": "Sender spoofing",
    "deceptive_links": "Deceptive links",
    "urgency_threats": "Urgency or threats",
    "unexpected_attachments": "Unexpected attachments",
    "credential_requests": "Credential requests",
    "lookalike_websites": "Look-alike websites",
}
CATEGORIES = (
    ("submit", "submit an answer"),
    ("page", "load a page"),
    ("turnaround", "answer to next page"),
    ("static", "static file"),
)
STATIC_ASSET = re.compile(r'(?:href|src)="(/static/[^"]+)"')
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
SCENARIO = re.compile(r'name="scenario_id" value="([ABP][0-9]{2})"')
STAT = re.compile(r'<p class="stat__value[^"]*">([+\-]?[0-9.]+)')
CUE_BARS = re.compile(
    r'aria-label="([^":]+): ([0-9]+)% correct before training, ([0-9]+)% after training"')
REDIRECTS = (301, 302, 303, 307, 308)
# The first figures on the administrator's dashboard. Every journey in this test
# runs to the end, so each count must equal the number of journeys.
DASHBOARD_COUNTS = (
    "consented", "finished the pre-assessment", "opened the lessons",
    "finished both assessments", "finished the survey",
)
VALUES_PER_RESULTS_PAGE = 3 + 2 * len(CUES)  # pre, post, and gain; before and after per cue
VALUES_PER_EXPORT_ROW = 5 + 2 * len(CUES)    # form order, three scores, SUS; cue counts


class Stats:
    """Thread-safe timings and errors for one concurrency level."""

    def __init__(self):
        self._lock = threading.Lock()
        self.timings = {name: [] for name, _label in CATEGORIES}
        self.requests = 0
        self.errors = []
        self.error_count = 0

    def count_request(self):
        with self._lock:
            self.requests += 1

    def add_timing(self, category, milliseconds):
        with self._lock:
            self.timings[category].append(milliseconds)

    def add_error(self, description):
        with self._lock:
            self.error_count += 1
            if len(self.errors) < 10:
                self.errors.append(description)


class Oracle:
    """Thread-safe record of expected values and how many reported values match."""

    def __init__(self):
        self._lock = threading.Lock()
        self.checked = 0
        self.matched = 0
        self.mismatches = []
        self.export_rows = []
        self.completed = 0

    def check(self, what, expected, actual):
        with self._lock:
            self.checked += 1
            if expected == actual:
                self.matched += 1
            elif len(self.mismatches) < 10:
                self.mismatches.append(f"{what}: expected {expected!r}, got {actual!r}")

    def add_journey(self, export_row):
        with self._lock:
            self.export_rows.append(export_row)
            self.completed += 1


class Browser:
    """A minimal HTTP client: one keep-alive connection and a cookie jar."""

    def __init__(self, base_url, stats, cafile=None, timeout=30):
        parts = urllib.parse.urlsplit(base_url)
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port or (443 if self._https else 80)
        # cafile adds one more trusted authority, for example a proxy's local one.
        self._context = ssl.create_default_context(cafile=cafile) if self._https else None
        self._timeout = timeout
        self._connection = None
        self._cookies = {}
        self._stats = stats

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _connect(self):
        if self._https:
            return http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout, context=self._context)
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    def send(self, method, path, form=None, headers=None):
        """One HTTP request. Returns (status, headers, body, milliseconds)."""
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        send_headers = {"Accept": "text/html,*/*", "User-Agent": "phishaware-loadtest/1.0"}
        if body is not None:
            send_headers["Content-Type"] = "application/x-www-form-urlencoded"
        if self._cookies:
            send_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        send_headers.update(headers or {})
        started = time.perf_counter()
        for attempt in (1, 2):   # one retry if the server closed an idle connection
            try:
                if self._connection is None:
                    self._connection = self._connect()
                self._connection.request(method, path, body=body, headers=send_headers)
                response = self._connection.getresponse()
                payload = response.read()
                break
            except (http.client.HTTPException, OSError) as error:
                self.close()
                if attempt == 2:
                    self._stats.add_error(f"{method} {path}: {type(error).__name__}: {error}")
                    return 0, {}, b"", 0.0
        elapsed = (time.perf_counter() - started) * 1000.0
        self._stats.count_request()
        for value in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = value.partition("=")
            self._cookies[name.strip()] = rest.split(";", 1)[0]
        if response.headers.get("Connection", "").lower() == "close":
            self.close()
        return response.status, response.headers, payload, elapsed

    def page(self, path, expect=200):
        """GET a screen, following redirects as a browser would. Returns its HTML."""
        total = 0.0
        for _ in range(6):
            status, headers, payload, elapsed = self.send("GET", path)
            total += elapsed
            if status in REDIRECTS:
                path = urllib.parse.urlsplit(headers["Location"]).path
                continue
            if status != expect:
                self._stats.add_error(f"GET {path}: status {status}, expected {expect}")
            self._stats.add_timing("page", total)
            return payload.decode("utf-8", "replace")
        self._stats.add_error(f"GET {path}: too many redirects")
        return ""

    def submit(self, path, form):
        """POST a form, then load the screen it redirects to. Returns that HTML."""
        status, headers, _payload, elapsed = self.send("POST", path, form=form)
        if status not in REDIRECTS:
            self._stats.add_error(f"POST {path}: status {status}, expected a redirect")
            return ""
        self._stats.add_timing("submit", elapsed)
        started = time.perf_counter()
        html = self.page(urllib.parse.urlsplit(headers["Location"]).path)
        self._stats.add_timing("turnaround", elapsed + (time.perf_counter() - started) * 1000.0)
        return html

    def static(self, path):
        status, _headers, _payload, elapsed = self.send("GET", path)
        if status != 200:
            self._stats.add_error(f"GET {path}: status {status}, expected 200")
        self._stats.add_timing("static", elapsed)


def make_plan(seed, index):
    """Scripted answers for one journey: which positions are wrong, and the ratings."""
    rng = random.Random(seed * 1_000_003 + index)
    return {
        "pre_wrong": set(rng.sample(range(12), rng.randint(0, 6))),
        "post_wrong": set(rng.sample(range(12), rng.randint(0, 6))),
        "practice_wrong": set(rng.sample(range(6), rng.randint(0, 2))),
        "ratings": [rng.randint(1, 5) for _ in range(10)],
    }


def oracle_sus(ratings):
    """The published scoring rule (Brooke, 1996), written independently of the application."""
    odd = sum(ratings[i] - 1 for i in range(0, 10, 2))
    even = sum(5 - ratings[i] for i in range(1, 10, 2))
    return (odd + even) * 2.5


def oracle_percent(correct, total=12):
    return round(correct / total * 100, 1)


def opposite(label):
    return "legitimate" if label == "phishing" else "phishing"


def token_and_item(html, bank):
    """The anti-forgery token and the scenario shown on a page, or (None, None)."""
    token, scenario = CSRF.search(html), SCENARIO.search(html)
    if token is None or scenario is None:
        return None, None
    return token.group(1), bank[scenario.group(1)]


def run_journey(index, args, bank, stats, oracle):
    """One participant's journey, with every reported figure checked against the oracle."""
    plan = make_plan(args.seed, index)
    browser = Browser(args.base_url, stats, cafile=args.cacert)
    correct = {"pre": dict.fromkeys(CUES, 0), "post": dict.fromkeys(CUES, 0)}
    forms = {}

    def assessment(phase):
        url = f"/assessment/{phase}"
        html = browser.page(url)
        for position in range(12):
            token, item = token_and_item(html, bank)
            if item is None:
                raise RuntimeError(f"{url}: item {position + 1} did not show a scenario")
            forms[phase] = item["pool"]
            wrong = position in plan[f"{phase}_wrong"]
            if not wrong:
                correct[phase][item["cue"]] += 1
            time.sleep(args.think_time)
            html = browser.submit(url, {
                "scenario_id": item["id"], "csrf_token": token,
                "answer": opposite(item["label"]) if wrong else item["label"]})

    try:
        html = browser.page("/consent")
        token = CSRF.search(html)
        if token is None:
            raise RuntimeError("the consent page has no anti-forgery token")
        for asset in sorted(set(STATIC_ASSET.findall(html))):
            browser.static(asset)
        browser.submit("/consent", {"adult": "yes", "agree": "yes", "csrf_token": token.group(1)})

        assessment("pre")
        browser.page("/learn")
        html = browser.page("/practice")
        for position in range(6):
            token, item = token_and_item(html, bank)
            if item is None:
                raise RuntimeError(f"/practice: item {position + 1} did not show a scenario")
            wrong = position in plan["practice_wrong"]
            time.sleep(args.think_time)
            browser.submit("/practice", {       # the redirect leads to the feedback screen
                "scenario_id": item["id"], "csrf_token": token,
                "answer": opposite(item["label"]) if wrong else item["label"]})
            html = browser.page("/practice")    # the next scenario, or the summary
        assessment("post")

        pre = oracle_percent(sum(correct["pre"].values()))
        post = oracle_percent(sum(correct["post"].values()))
        html = browser.page("/results")
        shown = [float(value) for value in STAT.findall(html)[:3]] + [None] * 3
        for label, expected, actual in zip(
                ("pre-assessment score", "post-assessment score", "gain"),
                (pre, post, round(post - pre, 1)), shown):
            oracle.check(f"journey {index}: results page, {label}", expected, actual)
        bars = {name: (int(before), int(after)) for name, before, after in CUE_BARS.findall(html)}
        for cue in CUES:
            before, after = bars.get(CUE_LABELS[cue], (None, None))
            oracle.check(f"journey {index}: results page, {cue} before",
                         round(correct["pre"][cue] / 2 * 100), before)
            oracle.check(f"journey {index}: results page, {cue} after",
                         round(correct["post"][cue] / 2 * 100), after)

        html = browser.page("/survey")
        form = {f"q{number}": str(rating) for number, rating in enumerate(plan["ratings"], 1)}
        form["csrf_token"] = CSRF.search(html).group(1)
        html = browser.submit("/survey", form)
        status, _headers, payload, _elapsed = browser.send(
            "POST", "/finish", form={"csrf_token": CSRF.search(html).group(1)})
        if status != 200 or b"signed out of this browser" not in payload:
            raise RuntimeError(f"/finish: status {status}; the session was not ended")
        oracle.add_journey(
            [forms["pre"] + forms["post"], pre, post, round(post - pre, 1),
             oracle_sus(plan["ratings"])]
            + [correct["pre"][cue] for cue in CUES] + [correct["post"][cue] for cue in CUES])
    except Exception as error:   # one broken journey must not stop the others
        stats.add_error(f"journey {index}: {type(error).__name__}: {error}")
    finally:
        browser.close()


def run_level(users, journeys, first_index, args, bank, oracle):
    """Run `journeys` journeys with `users` of them in progress at any time."""
    stats = Stats()
    indexes = iter(range(first_index, first_index + journeys))
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                index = next(indexes, None)
            if index is None:
                return
            run_journey(index, args, bank, stats, oracle)

    completed_before = oracle.completed
    started = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(users)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    seconds = time.perf_counter() - started
    completed = oracle.completed - completed_before
    return {
        "users": users,
        "journeys": journeys,
        "completed": completed,
        "requests": stats.requests,
        "failed": stats.error_count,
        "errors": stats.errors,
        "seconds": round(seconds, 2),
        "requests_per_second": round(stats.requests / seconds, 1),
        "journeys_per_minute": round(completed / seconds * 60, 1),
        "latency_ms": {name: summarise(values) for name, values in stats.timings.items()},
    }


def summarise(values):
    """Count, median, percentiles (nearest rank), mean, and maximum in milliseconds."""
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)

    def percentile(p):
        return round(ordered[max(1, math.ceil(p * count / 100)) - 1], 1)

    return {
        "count": count, "mean": round(statistics.fmean(ordered), 1),
        "p50": percentile(50), "p90": percentile(90), "p95": percentile(95),
        "p99": percentile(99), "max": round(ordered[-1], 1),
    }


def check_administrator_view(args, oracle):
    """Compare the dashboard counts and the export with what the journeys must produce."""
    stats = Stats()
    browser = Browser(args.base_url, stats, cafile=args.cacert)
    try:
        html = browser.page("/admin/login")
        html = browser.submit("/admin/login", {
            "username": args.admin_user, "password": args.admin_password,
            "csrf_token": CSRF.search(html).group(1)})
        counts = [int(float(value)) for value in STAT.findall(html)[:len(DASHBOARD_COUNTS)]]
        for label, count in zip(DASHBOARD_COUNTS, counts + [None] * len(DASHBOARD_COUNTS)):
            oracle.check(f"dashboard: {label}", oracle.completed, count)
        status, _headers, payload, _elapsed = browser.send("GET", "/admin/export.csv")
        if status != 200:
            stats.add_error(f"export: status {status}")
            return stats, None
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8"))))[1:]
        actual = sorted([row[1]] + [float(value) for value in row[2:6]]
                        + [int(value) for value in row[6:]] for row in rows)
        expected = sorted(oracle.export_rows)
        oracle.check("export: number of rows", len(expected), len(actual))
        for number, (want, got) in enumerate(zip(expected, actual), start=1):
            for column, (a, b) in enumerate(zip(want, got)):
                oracle.check(f"export row {number}, column {column}", a, b)
        return stats, len(rows)
    except Exception as error:
        stats.add_error(f"administrator check: {type(error).__name__}: {error}")
        return stats, None
    finally:
        browser.close()


def format_report(report):
    """The plain-text report: one block per measure."""
    lines = [
        f"PhishAware load test: {report['base_url']}",
        f"  seed {report['seed']}, think time {report['think_time_s']:g} s, "
        f"{report['requests_per_journey']} HTTP requests per journey",
        "",
        "Throughput",
        f"  {'users':>5}{'journeys':>10}{'requests':>10}{'failed':>8}{'seconds':>9}"
        f"{'requests/s':>12}{'journeys/min':>14}",
    ]
    for level in report["levels"]:
        lines.append(
            f"  {level['users']:>5}{level['completed']:>10}{level['requests']:>10}"
            f"{level['failed']:>8}{level['seconds']:>9.1f}{level['requests_per_second']:>12.1f}"
            f"{level['journeys_per_minute']:>14.1f}")
    lines += ["", "Latency in milliseconds",
              f"  {'measure':<21}{'users':>5}{'count':>8}{'median':>9}{'p95':>9}{'p99':>9}"
              f"{'max':>9}"]
    for name, label in CATEGORIES:
        for level in report["levels"]:
            stats = level["latency_ms"][name]
            if stats is None:
                continue
            lines.append(
                f"  {label:<21}{level['users']:>5}{stats['count']:>8}{stats['p50']:>9.1f}"
                f"{stats['p95']:>9.1f}{stats['p99']:>9.1f}{stats['max']:>9.1f}")
            label = ""
    if report["budget"]:
        lines.append("")
        for check in report["budget"]:
            verdict = "PASS" if check["passed"] else "FAIL"
            lines.append(
                f"Budget at {check['users']} users: {check['measure']} p95 "
                f"{check['p95_ms']:.1f} ms <= {check['limit_ms']:g} ms  {verdict}")
    accuracy = report["accuracy"]
    lines += [
        "",
        f"Result accuracy: {accuracy['matched']:,} of {accuracy['checked']:,} values reported "
        "by the system match the independent oracle",
        f"  {accuracy['journeys']} journeys x {VALUES_PER_RESULTS_PAGE} results-page checks"
        + (f"; {accuracy['export_rows']} export rows x {VALUES_PER_EXPORT_ROW} values; "
           f"{len(DASHBOARD_COUNTS)} dashboard counts; 1 row count"
           if accuracy["export_rows"] is not None else ""),
    ]
    for line in report["problems"]:
        lines.append(f"  ! {line}")
    return lines


def format_markdown(report):
    """The same results as Markdown tables, for a CI run summary."""
    lines = ["### Load test", "",
             "| Users | Journeys | Requests | Failed | Requests/s | Submit p95 (ms) | "
             "Page p95 (ms) | Turnaround p95 (ms) |", "|---|---|---|---|---|---|---|---|"]
    for level in report["levels"]:
        p95 = [(level["latency_ms"][name] or {}).get("p95", "n/a")
               for name in ("submit", "page", "turnaround")]
        lines.append(
            f"| {level['users']} | {level['completed']} | {level['requests']} | "
            f"{level['failed']} | {level['requests_per_second']} | {p95[0]} | {p95[1]} | "
            f"{p95[2]} |")
    accuracy = report["accuracy"]
    lines += ["", f"Result accuracy: **{accuracy['matched']:,} of {accuracy['checked']:,}** "
              "reported values match the independent oracle.", ""]
    return lines


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--levels", default="5",
                        help="concurrent participants to test, for example 1,5,10,25,50")
    parser.add_argument("--min-journeys", type=int, default=1,
                        help="run at least this many journeys at every level")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--think-time", type=float, default=0.0,
                        help="seconds a participant waits before each answer")
    parser.add_argument("--bank", default=str(
        Path(__file__).resolve().parent.parent / "data" / "scenarios.json"))
    parser.add_argument("--cacert", help="extra certificate authority to trust (PEM file)")
    parser.add_argument("--admin-user")
    parser.add_argument("--admin-password")
    parser.add_argument("--budget-users", type=int, default=25,
                        help="the level at which the NFR-01 budgets are checked")
    parser.add_argument("--budget-submit-ms", type=float, default=500.0)
    parser.add_argument("--budget-page-ms", type=float, default=2000.0)
    parser.add_argument("--json", help="write the full result to this file")
    parser.add_argument("--markdown", help="append Markdown tables to this file")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    with open(args.bank, encoding="utf-8") as handle:
        bank = {item["id"]: item for item in json.load(handle)["scenarios"]}
    oracle = Oracle()
    levels, first_index = [], 0
    for users in [int(value) for value in args.levels.split(",")]:
        journeys = max(users, math.ceil(args.min_journeys / users) * users)
        levels.append(run_level(users, journeys, first_index, args, bank, oracle))
        first_index += journeys

    problems = [line for level in levels for line in level["errors"]]
    exported = None
    if args.admin_user and args.admin_password:
        admin_stats, exported = check_administrator_view(args, oracle)
        problems += admin_stats.errors
    problems += oracle.mismatches

    budget = []
    for level in levels:
        if level["users"] != args.budget_users:
            continue
        for measure, limit in (("submit", args.budget_submit_ms), ("page", args.budget_page_ms)):
            stats = level["latency_ms"][measure]
            if stats is not None:
                budget.append({"users": level["users"], "measure": measure, "limit_ms": limit,
                               "p95_ms": stats["p95"], "passed": stats["p95"] <= limit})

    started = sum(level["journeys"] for level in levels)
    report = {
        "base_url": args.base_url,
        "seed": args.seed,
        "think_time_s": args.think_time,
        "requests_per_journey": round(levels[0]["requests"] / max(1, levels[0]["completed"])),
        "levels": levels,
        "budget": budget,
        "accuracy": {"checked": oracle.checked, "matched": oracle.matched,
                     "journeys": oracle.completed, "export_rows": exported},
        "problems": problems,
    }
    for line in format_report(report):
        print(line)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as handle:
            handle.write("\n".join(format_markdown(report)) + "\n")

    failed = (problems or oracle.completed != started or oracle.matched != oracle.checked
              or any(not check["passed"] for check in budget))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
