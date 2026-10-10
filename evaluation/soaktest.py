"""Endurance ("soak") test for a running PhishAware instance: does it stay stable?

    python evaluation/soaktest.py --base-url http://127.0.0.1:5000 --users 25 --minutes 10 \\
        --admin-user evaluator --admin-password "$PASSWORD" \\
        --memory-command "docker compose exec -T app cat /sys/fs/cgroup/memory.stat"

The load test (loadtest.py) answers "how fast under a burst": every simulated
participant answers at once, and a level lasts seconds. This test answers a
different question. A fixed number of participants work at a human pace (one
second of thinking before each answer by default), a new participant starts as
soon as one has finished, and this continues for minutes. The test reports, for
every minute, how many requests were served, how many failed, the response
times, and the memory of the web service, and then checks four things:

1. Reliability. No request failed.
2. Correctness. Every score the system reported matches the independent oracle
   of loadtest.py, on the results pages, the dashboard, and the export.
3. No slowdown. The 95th percentile of answers in the last third of the run is
   at most 1.5 times that of the first third (or below 100 ms, where such a
   ratio only measures noise), and the whole run meets the limits of NFR-01.
4. No leak. Once the service is warm, its memory must stay level: the memory
   in the last third of the run is at most 1.15 times that in the middle
   third. The first third is not judged, because memory rises there for a
   reason that ends. The server loads the application once and forks its
   workers; a worker shares that memory until it first writes to a page, and
   each of its threads takes memory of its own as it serves its first
   requests. In the pipeline and on a developer machine that rise ended after
   about 18,000 requests. A run that serves fewer than 12,000 requests in its
   first third has not left the warm-up, and its memory is reported but not
   judged. Skipped without --memory-command. The command prints a number of
   bytes, or the text of a control group's memory.stat, from which "anon" is
   taken: the memory of the processes, without the kernel's file cache.

Start from an empty test database so that the dashboard and export checks see
only this run. Minutes are not weeks: the test shows whether something drifts
within the run, and it cannot show what happens over a whole pilot.
"""

import argparse
import json
import math
import shlex
import statistics
import subprocess  # nosec B404 - runs only the memory command given on the command line
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loadtest  # noqa: E402  (the journey, the HTTP client, and the oracle are shared)

SLOWDOWN_LIMIT = 1.5     # last third against first third, 95th percentile of answers
SLOWDOWN_FLOOR_MS = 100  # below this the ratio is not judged
MEMORY_LIMIT = 1.15      # last third against middle third, median of the samples
WARM_UP_REQUESTS = 12_000   # served in the first third before the memory check is judged


class TimedStats(loadtest.Stats):
    """Timings and errors that remember when they happened, for the per-minute table."""

    def __init__(self, started):
        super().__init__()
        self.started = started
        self.samples = []         # (seconds since the start, category, milliseconds)
        self.error_times = []

    def add_timing(self, category, milliseconds):
        super().add_timing(category, milliseconds)
        with self._lock:
            self.samples.append((time.monotonic() - self.started, category, milliseconds))

    def add_error(self, description):
        super().add_error(description)
        with self._lock:
            self.error_times.append(time.monotonic() - self.started)


def percentile(values, share):
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(1, math.ceil(share * len(ordered) / 100)) - 1], 1)


def parse_memory(text):
    """Bytes in the output of the memory command: memory.stat's "anon", or the first number."""
    lines = [line.split() for line in text.strip().splitlines()]
    for words in lines:
        if len(words) == 2 and words[0] == "anon":
            return int(words[1])
    return int(lines[0][0])


def read_memory(command):
    """Bytes reported by the memory command, or None when it fails."""
    try:
        result = subprocess.run(  # nosec B603 - the tester's own command, no shell
            shlex.split(command), capture_output=True, text=True, timeout=20)
        return parse_memory(result.stdout)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def run(args, bank):
    """Keep `users` journeys in progress for `minutes`.

    Returns the timings, the oracle, the memory samples, the number of journeys
    that were begun, and the duration in seconds.
    """
    started = time.monotonic()
    deadline = started + args.minutes * 60
    stats, oracle = TimedStats(started), loadtest.Oracle()
    counter = iter(range(10 ** 9))
    lock = threading.Lock()
    memory = []                   # (seconds since the start, bytes)
    stop = threading.Event()

    def participant():
        while time.monotonic() < deadline:
            with lock:
                index = next(counter)
            loadtest.run_journey(index, args, bank, stats, oracle)

    def sampler():
        while not stop.is_set():
            value = read_memory(args.memory_command)
            if value is not None:
                memory.append((time.monotonic() - started, value))
            stop.wait(args.sample_seconds)

    threads = [threading.Thread(target=participant, daemon=True) for _ in range(args.users)]
    watcher = threading.Thread(target=sampler, daemon=True) if args.memory_command else None
    if watcher:
        watcher.start()
    for thread in threads:
        thread.start()
        time.sleep(args.minutes * 60 / args.users / 20)   # arrivals spread over the first 5%
    for thread in threads:
        thread.join()
    stop.set()
    if watcher:
        watcher.join()
        final = read_memory(args.memory_command)
        if final is not None:
            memory.append((time.monotonic() - started, final))
    with lock:
        begun = next(counter)
    return stats, oracle, memory, begun, time.monotonic() - started


def windows(stats, memory, seconds):
    """One row per minute of the run."""
    rows = []
    for minute in range(max(1, math.ceil(seconds / 60))):
        low, high = minute * 60, (minute + 1) * 60
        inside = [sample for sample in stats.samples if low <= sample[0] < high]
        answers = [ms for _at, category, ms in inside if category == "submit"]
        pages = [ms for _at, category, ms in inside if category == "page"]
        held = [value for at, value in memory if low <= at < high]
        rows.append({
            "minute": minute + 1,
            "answers": len(answers), "pages": len(pages),
            "failed": sum(1 for at in stats.error_times if low <= at < high),
            "answer_p50": percentile(answers, 50), "answer_p95": percentile(answers, 95),
            "page_p50": percentile(pages, 50), "page_p95": percentile(pages, 95),
            "memory_mib": round(statistics.median(held) / 2 ** 20, 1) if held else None,
        })
    return rows


def thirds(pairs, seconds):
    """The values of the first, the middle, and the last third of the run."""
    first = [value for at, value in pairs if at < seconds / 3]
    middle = [value for at, value in pairs if seconds / 3 <= at < seconds * 2 / 3]
    last = [value for at, value in pairs if at >= seconds * 2 / 3]
    return first, middle, last


def judge(args, stats, oracle, memory, begun, seconds, exported):
    """The four checks; each is (name, passed or None when not judged, what was measured)."""
    answers = [(at, ms) for at, category, ms in stats.samples if category == "submit"]
    pages = [ms for _at, category, ms in stats.samples if category == "page"]
    first, _middle, last = thirds(answers, seconds)
    early, late = percentile(first, 95), percentile(last, 95)
    answer_p95, page_p95 = percentile([ms for _at, ms in answers], 95), percentile(pages, 95)
    checks = [
        ("Reliability: no failed request", stats.error_count == 0,
         f"{stats.error_count} of {stats.requests:,} requests failed; "
         f"{oracle.completed} of {begun} journeys completed"),
        ("Correctness: reported values match the oracle",
         oracle.matched == oracle.checked and oracle.completed == begun,
         f"{oracle.matched:,} of {oracle.checked:,} values"
         + ("" if exported is None else f", including {exported} export rows")),
    ]
    if early is None or late is None:
        checks.append(("No slowdown: last third against first third", None,
                       "the run was too short to compare"))
    else:
        ratio = late / early if early else float("inf")
        checks.append((
            "No slowdown: last third against first third",
            ratio <= SLOWDOWN_LIMIT or late <= SLOWDOWN_FLOOR_MS,
            f"answers p95 {early:.1f} ms, then {late:.1f} ms (ratio {ratio:.2f}, limit "
            f"{SLOWDOWN_LIMIT} or under {SLOWDOWN_FLOOR_MS} ms)"))
    checks.append((
        "NFR-01 over the whole run",
        answer_p95 is not None and answer_p95 <= args.budget_submit_ms
        and page_p95 <= args.budget_page_ms,
        f"answers p95 {answer_p95} ms (limit {args.budget_submit_ms:g}), "
        f"pages p95 {page_p95} ms (limit {args.budget_page_ms:g})"))
    checks.append(judge_memory(memory, stats.requests, seconds))
    return checks


def judge_memory(memory, requests, seconds):
    """The fourth check: does the memory stay level once the service is warm?"""
    name = "No leak: memory of the web service, last third against middle third"
    if not memory:
        return name, None, "not measured (no --memory-command, or it gave no number)"
    held = [statistics.median(part) / 2 ** 20 if part else None
            for part in thirds(memory, seconds)]
    if None in held:
        return name, None, "too few memory samples to compare"
    start, middle, end = held
    measured = (f"{middle:.1f} MiB, then {end:.1f} MiB (ratio {end / middle:.2f}, limit "
                f"{MEMORY_LIMIT}); first third {start:.1f} MiB")
    if requests / 3 < WARM_UP_REQUESTS:
        return name, None, (measured + f"; still warming up after {requests / 3:,.0f} "
                            f"requests in the first third (judged from {WARM_UP_REQUESTS:,})")
    return name, end <= MEMORY_LIMIT * middle, measured


def show(value, width, digits=1):
    return f"{'-':>{width}}" if value is None else f"{value:>{width}.{digits}f}"


def format_report(report):
    lines = [
        f"PhishAware endurance test: {report['base_url']}",
        f"  {report['users']} participants at a time for {report['minutes']:g} minutes, "
        f"{report['think_time_s']:g} s of thinking before each answer, seed {report['seed']}",
        f"  {report['journeys']} journeys completed, {report['requests']:,} requests, "
        f"{report['requests_per_second']:.1f} requests per second",
        "",
        f"  {'minute':>6}{'answers':>9}{'pages':>8}{'failed':>8}{'answer p50':>12}"
        f"{'answer p95':>12}{'page p50':>10}{'page p95':>10}{'memory MiB':>12}",
    ]
    for row in report["windows"]:
        lines.append(
            f"  {row['minute']:>6}{row['answers']:>9}{row['pages']:>8}{row['failed']:>8}"
            f"{show(row['answer_p50'], 12)}{show(row['answer_p95'], 12)}"
            f"{show(row['page_p50'], 10)}{show(row['page_p95'], 10)}"
            f"{show(row['memory_mib'], 12)}")
    lines.append("")
    for name, passed, measured in report["checks"]:
        verdict = "not judged" if passed is None else ("PASS" if passed else "FAIL")
        lines.append(f"{name}: {measured}  {verdict}")
    for problem in report["problems"]:
        lines.append(f"  ! {problem}")
    failed = sum(1 for _name, passed, _measured in report["checks"] if passed is False)
    lines += ["", f"Result: {'PASS' if not failed else f'{failed} check(s) failed'}"]
    return lines


def format_markdown(report):
    lines = ["### Endurance test", "",
             f"{report['users']} participants at a time for {report['minutes']:g} minutes: "
             f"{report['journeys']} journeys, {report['requests']:,} requests.", "",
             "| Check | Measured | Verdict |", "|---|---|---|"]
    for name, passed, measured in report["checks"]:
        verdict = "not judged" if passed is None else ("pass" if passed else "**fail**")
        lines.append(f"| {name} | {measured} | {verdict} |")
    return lines + [""]


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--users", type=int, default=25,
                        help="participants working at the same time (NFR-01 names 25)")
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--think-time", type=float, default=1.0,
                        help="seconds a participant waits before each answer")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--bank", default=str(
        Path(__file__).resolve().parent.parent / "data" / "scenarios.json"))
    parser.add_argument("--cacert", help="extra certificate authority to trust (PEM file)")
    parser.add_argument("--admin-user")
    parser.add_argument("--admin-password")
    parser.add_argument("--memory-command",
                        help="a command that prints the web service's memory in bytes")
    parser.add_argument("--sample-seconds", type=float, default=15.0,
                        help="time between two memory samples")
    parser.add_argument("--budget-submit-ms", type=float, default=500.0)
    parser.add_argument("--budget-page-ms", type=float, default=2000.0)
    parser.add_argument("--json", help="write the full result to this file")
    parser.add_argument("--markdown", help="append Markdown tables to this file")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    with open(args.bank, encoding="utf-8") as handle:
        bank = {item["id"]: item for item in json.load(handle)["scenarios"]}
    stats, oracle, memory, begun, seconds = run(args, bank)
    problems = list(stats.errors)
    exported = None
    if args.admin_user and args.admin_password:
        admin_stats, exported = loadtest.check_administrator_view(args, oracle)
        problems += admin_stats.errors
    problems += oracle.mismatches
    report = {
        "base_url": args.base_url, "users": args.users, "minutes": args.minutes,
        "think_time_s": args.think_time, "seed": args.seed,
        "journeys": oracle.completed, "requests": stats.requests,
        "requests_per_second": round(stats.requests / seconds, 1),
        "seconds": round(seconds, 1),
        "windows": windows(stats, memory, seconds),
        "checks": judge(args, stats, oracle, memory, begun, seconds, exported),
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
    return 1 if problems or any(passed is False for _n, passed, _m in report["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
