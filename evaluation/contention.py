"""Contention test: the processes and threads of the deployed service on one database file.

    python evaluation/contention.py                   two processes x four threads, as deployed
    python evaluation/contention.py --delay-us 0      the same without the delayed close()

The web service runs as two processes with four threads each (gunicorn.conf.py),
and all eight use one SQLite file. This test builds that arrangement without a
network in between: every thread of every process takes complete participant
journeys through the application (Flask's test client) as fast as it can, so
that requests of both processes meet in the database the whole time. The
journeys and the oracle are those of loadtest.py.

The test exists because of defect D-7 (docs/test-report.md). SQLite locks the
database file with POSIX locks, and a process loses all its locks on a file
when it closes any descriptor of that file. Up to release 0.6.0 every request
closed its connection, and now and then that close cancelled the write lock
that another thread of the process had just taken. Two processes then wrote at
once: a request failed with "disk I/O error", and answers that had been stored
were overwritten. This happened about once in a hundred thousand requests,
which no test of a few seconds would find. The test therefore makes close() on
the database file take 0.3 ms longer (slowclose.c, Linux only). With that
delay release 0.6.0 fails within seconds, and a correct release gives the same
results as without it.

The test passes when all of the following hold:

1. Every journey was completed and no request was answered with a server error.
2. Every figure the system reported matches the oracle: on the results pages,
   and afterwards on the administrator's dashboard and in the export, which show
   what the database really holds.
3. The database passes SQLite's integrity check and its foreign-key check, and
   holds exactly the records that the journeys must have produced.
4. Each server process still has every connection open that it opened.

The script needs the application's own packages (requirements.txt), and a C
compiler for the delay.
"""

import argparse
import json
import os
import platform
import secrets
import shutil
import sqlite3
import statistics
import subprocess  # nosec B404 - starts the C compiler and copies of this script only
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import loadtest  # noqa: E402  (the journey and the oracle are shared)

DATABASE_NAME = "phishaware.db"
POOL = "phishaware.connections"     # src/db.py; absent up to release 0.6.0
START_DELAY_S = 4                   # time for every process to load the application
# What one completed journey leaves in the database.
RECORDS_PER_JOURNEY = {
    "participant": 1, "attempt": 3, "response": 30, "lesson_view": 1,
    "sus_response": 1, "session_end": 1,
}


class InProcessBrowser(loadtest.Browser):
    """The load test's browser, calling the application directly instead of over HTTP."""

    def __init__(self, app, stats, server_errors=None):
        self._client = app.test_client()
        self._stats = stats
        self._server_errors = [] if server_errors is None else server_errors

    def close(self):
        pass

    def send(self, method, path, form=None, headers=None):
        started = time.perf_counter()
        response = self._client.open(path, method=method, data=form, headers=headers)
        payload = response.get_data()
        response.close()
        elapsed = (time.perf_counter() - started) * 1000.0
        self._stats.count_request()
        if response.status_code >= 500:
            self._server_errors.append(f"{method} {path}: status {response.status_code}")
        return response.status_code, response.headers, payload, elapsed


def app_config(database, journal):
    return {
        "DATABASE": database, "SQLITE_JOURNAL_MODE": journal,
        "SECRET_KEY": "contention-test-" + "0" * 48, "PROPAGATE_EXCEPTIONS": False,
    }


def close_time_us(path):
    """Microseconds that close() takes on a descriptor of `path` (median of five)."""
    times = []
    for _ in range(5):
        descriptor = os.open(path, os.O_RDONLY)
        started = time.perf_counter()
        os.close(descriptor)
        times.append((time.perf_counter() - started) * 1e6)
    return statistics.median(times)


# One server process ---------------------------------------------------------------
def serve(args):
    """Take journeys in `threads` threads and print what happened as one line of JSON."""
    # Measured first: this process holds no lock yet that a close() could cancel.
    close_us = close_time_us(args.database)

    from flask import got_request_exception, request
    from src.app import create_app

    app = create_app(app_config(args.database, args.journal))
    app.logger.setLevel("CRITICAL")     # the exceptions are collected below
    exceptions = []

    def note(_sender, exception, **_extra):
        name = getattr(exception, "sqlite_errorname", None)
        exceptions.append(f"{request.method} {request.path}: {type(exception).__name__}"
                          + (f" ({name})" if name else "") + f": {exception}")

    got_request_exception.connect(note, app)
    with open(args.bank, encoding="utf-8") as handle:
        bank = {item["id"]: item for item in json.load(handle)["scenarios"]}
    stats, oracle, server_errors = loadtest.Stats(), loadtest.Oracle(), []
    journey_args = argparse.Namespace(seed=args.seed, think_time=0.0, base_url="", cacert=None)

    def run(thread):
        for number in range(args.journeys):
            index = args.serve * 1_000_000 + thread * 10_000 + number
            loadtest.run_journey(index, journey_args, bank, stats, oracle,
                                 browser=InProcessBrowser(app, stats, server_errors))

    late = time.time() - args.start_at      # every process begins at the same moment
    if late < 0:
        time.sleep(-late)
    started = time.perf_counter()
    threads = [threading.Thread(target=run, args=(thread,)) for thread in range(args.threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    seconds = time.perf_counter() - started
    pool = app.extensions.get(POOL)
    print(json.dumps({
        "process": args.serve, "late_s": round(max(late, 0.0), 2), "close_us": round(close_us),
        "seconds": round(seconds, 2), "requests": stats.requests,
        "failed": stats.error_count, "errors": stats.errors,
        "server_errors": len(server_errors), "exception_count": len(exceptions),
        "exceptions": exceptions[:10],
        "completed": oracle.completed, "checked": oracle.checked, "matched": oracle.matched,
        "mismatches": oracle.mismatches, "export_rows": oracle.export_rows,
        "slowest_ms": {name: round(max(values), 1)
                       for name, values in stats.timings.items() if values},
        "connections": None if pool is None else {"opened": pool.opened, "open": pool.idle},
    }))
    return 0


# The test -------------------------------------------------------------------------
def build_delay_library(folder):
    """Compile slowclose.c. Returns (path of the library, None) or (None, the reason)."""
    if platform.system() != "Linux":
        return None, "the delay works on Linux only (LD_PRELOAD and /proc)"
    compiler = shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        return None, "no C compiler (cc or gcc) was found"
    library = Path(folder) / "slowclose.so"
    done = subprocess.run(  # nosec B603 - fixed arguments, no shell
        [compiler, "-O2", "-shared", "-fPIC", "-o", str(library), str(HERE / "slowclose.c"),
         "-ldl"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return None, "the compiler failed: " + (done.stderr.strip() or "no message")[-300:]
    return library, None


def start_processes(args, database, library):
    environment = dict(os.environ)
    if library is not None:
        environment["LD_PRELOAD"] = " ".join(
            part for part in (str(library), environment.get("LD_PRELOAD")) if part)
        environment["SLOWCLOSE_SUFFIX"] = "/" + DATABASE_NAME
        environment["SLOWCLOSE_USEC"] = str(args.delay_us)
    start_at = time.time() + START_DELAY_S
    return [
        subprocess.Popen(  # nosec B603 - this script, with arguments built here
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(number),
             "--database", database, "--threads", str(args.threads),
             "--journeys", str(args.journeys), "--seed", str(args.seed),
             "--journal", args.journal, "--bank", args.bank, "--start-at", repr(start_at)],
            env=environment, stdout=subprocess.PIPE, text=True)
        for number in range(args.processes)
    ]


def collect(processes, timeout):
    """Wait for the server processes. Returns (their results, problems)."""
    results, problems = [], []
    deadline = time.monotonic() + timeout
    for number, process in enumerate(processes):
        try:
            output, _ = process.communicate(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            problems.append(f"process {number} had not finished after {timeout:g} s")
            continue
        try:
            results.append(json.loads(output.strip().splitlines()[-1]))
        except (IndexError, ValueError):
            problems.append(f"process {number} ended with status {process.returncode} "
                            "and reported nothing")
    return results, problems


def inspect_database(database):
    """SQLite's own checks and the number of rows per table, read from the file directly."""
    connection = sqlite3.connect(database)
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        orphans = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        counts = {}
        for table in RECORDS_PER_JOURNEY:       # names from the constant above, not from input
            counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]   # nosec B608
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
    except sqlite3.DatabaseError as error:
        return {"integrity": [f"{type(error).__name__}: {error}"], "orphans": None,
                "counts": {}, "journal": "unknown"}
    finally:
        connection.close()
    return {"integrity": integrity, "orphans": orphans, "counts": counts, "journal": journal}


def check_administrator_view(config, oracle):
    """Sign in as an administrator and compare the dashboard and the export with the oracle."""
    from src.app import create_app

    app = create_app(config)
    app.logger.setLevel("CRITICAL")
    password = secrets.token_urlsafe(18)
    created = app.test_cli_runner().invoke(
        args=["create-admin", "--username", "evaluator", "--password", password])
    if created.exit_code != 0:
        return [f"the administrator could not be created: {created.output.strip()[-200:]}"], None
    admin_args = argparse.Namespace(
        admin_user="evaluator", admin_password=password, base_url="", cacert=None)
    stats, exported = loadtest.check_administrator_view(
        admin_args, oracle, browser_for=lambda stats: InProcessBrowser(app, stats))
    if POOL in app.extensions:
        app.extensions[POOL].close_idle()   # this process has one thread, and it is done
    return list(stats.errors), exported


def judge(args, started, results, oracle, database_state, exported, delay):
    """The four conditions of the module's docstring as (name, passed, measured)."""
    def total(key):
        return sum(result[key] for result in results)

    requests, failed = total("requests"), total("failed")
    checks = [
        ("Journeys completed", oracle.completed == started,
         f"{oracle.completed:,} of {started:,}"),
        ("Requests answered without a server error",
         failed == 0 and total("server_errors") == 0 and total("exception_count") == 0,
         f"{requests:,} requests, {total('server_errors'):,} answered with status 5xx, "
         f"{failed:,} steps of a journey failed"),
        ("Reported figures match the oracle", oracle.matched == oracle.checked,
         f"{oracle.matched:,} of {oracle.checked:,}"
         + ("" if exported is not None else " (the administrator's view was not read)")),
    ]
    expected = {table: per_journey * started
                for table, per_journey in RECORDS_PER_JOURNEY.items()}
    sound = (database_state["integrity"] == ["ok"] and database_state["orphans"] == 0
             and database_state["counts"] == expected)
    if database_state["integrity"] != ["ok"]:
        measured = "integrity check: " + "; ".join(database_state["integrity"][:3])
    else:
        counts = database_state["counts"]
        measured = ("integrity check ok, "
                    f"{database_state['orphans']} rows without their parent row; "
                    + ", ".join(f"{counts[table]:,} of {expected[table]:,} {table} rows"
                                for table in ("participant", "attempt", "response",
                                              "sus_response")))
    checks.append(("Database sound and complete", sound, measured))
    pools = [result["connections"] for result in results]
    if pools and all(pool is not None for pool in pools):
        opened, still_open = (sum(pool[key] for pool in pools) for key in ("opened", "open"))
        checks.append(("Connections kept open by the server processes", opened == still_open,
                       f"{still_open} of {opened} "
                       f"({' + '.join(str(pool['opened']) for pool in pools)})"))
    else:
        checks.append(("Connections kept open by the server processes", False,
                       "this release closes its connection after every request"))
    if delay["requested_us"]:
        observed = min((result["close_us"] for result in results), default=0)
        # The delay must really have been in force, or the test proves less than it says.
        checks.append(("close() on the database file was delayed",
                       observed >= 0.8 * delay["requested_us"],
                       f"{delay['requested_us']} µs requested, {observed} µs measured"))
    return checks


def format_report(report):
    delay = report["delay"]
    lines = [
        f"PhishAware contention test: {report['processes']} processes x {report['threads']} "
        f"threads on one database file, {report['journeys_per_thread']} journeys per thread",
        f"  Python {report['python']}, SQLite {report['sqlite']}, journal mode "
        f"{report['journal']}, seed {report['seed']}",
        "  close() on the database file "
        + (f"delayed by {delay['requested_us']} µs" if delay["requested_us"]
           else "not delayed"),
        f"  {report['requests']:,} requests in {report['seconds']:.1f} s "
        f"({report['requests_per_second']:.0f} per second); slowest answer "
        f"{report['slowest_ms'].get('submit', 0):.0f} ms, slowest page "
        f"{report['slowest_ms'].get('page', 0):.0f} ms",
        "",
    ]
    for name, passed, measured in report["checks"]:
        lines.append(f"{name}: {measured}  {'PASS' if passed else 'FAIL'}")
    for problem in report["problems"][:20]:
        lines.append(f"  ! {problem}")
    if len(report["problems"]) > 20:
        lines.append(f"  ! and {len(report['problems']) - 20} more")
    failed = sum(1 for _name, passed, _measured in report["checks"] if not passed)
    lines += ["", f"Result: {'PASS' if not failed else f'{failed} check(s) failed'}"]
    return lines


def format_markdown(report):
    delay = report["delay"]["requested_us"]
    lines = ["### Contention test", "",
             f"{report['processes']} processes x {report['threads']} threads on one database "
             f"file, close() delayed by {delay} µs: {report['requests']:,} requests.", "",
             "| Check | Measured | Verdict |", "|---|---|---|"]
    for name, passed, measured in report["checks"]:
        lines.append(f"| {name} | {measured} | {'pass' if passed else '**fail**'} |")
    return lines + [""]


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--processes", type=int, default=2,
                        help="server processes (WEB_CONCURRENCY in the deployment)")
    parser.add_argument("--threads", type=int, default=4,
                        help="threads in each process (PHISHAWARE_THREADS)")
    parser.add_argument("--journeys", type=int, default=25,
                        help="journeys that each thread takes, one after another")
    parser.add_argument("--delay-us", type=int, default=300,
                        help="microseconds added to close() on the database file; 0 for none")
    parser.add_argument("--journal", default="AUTO", choices=("AUTO", "WAL", "DELETE"))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--bank", default=str(ROOT / "data" / "scenarios.json"))
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds after which a server process is stopped")
    parser.add_argument("--json", help="write the full result to this file")
    parser.add_argument("--markdown", help="append Markdown tables to this file")
    # Used by the test itself, to start its server processes.
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--database", help=argparse.SUPPRESS)
    parser.add_argument("--start-at", type=float, default=0.0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.serve is None and args.processes * args.threads * args.journeys < 5:
        parser.error("the dashboard reports from five participants: run at least five journeys")
    return args


def main(argv=None):
    args = parse_arguments(argv)
    if args.serve is not None:
        return serve(args)

    from src.app import create_app

    with tempfile.TemporaryDirectory(prefix="phishaware-contention-") as folder:
        database = str(Path(folder) / DATABASE_NAME)
        config = app_config(database, args.journal)
        create_app(config)      # the schema and the scenario bank, before any process starts
        library = None
        if args.delay_us:
            library, reason = build_delay_library(folder)
            if library is None:
                print(f"The delayed close() could not be set up: {reason}.\n"
                      "Without it this test would prove much less than it reports. "
                      "Use --delay-us 0 to run it without the delay.", file=sys.stderr)
                return 2
        results, problems = collect(start_processes(args, database, library), args.timeout)
        seconds = max((result["seconds"] for result in results), default=0.0)

        oracle = loadtest.Oracle()
        for result in results:
            oracle.checked += result["checked"]
            oracle.matched += result["matched"]
            oracle.completed += result["completed"]
            oracle.export_rows += result["export_rows"]
            oracle.mismatches += result["mismatches"]
            problems += result["errors"] + result["exceptions"]
        database_state = inspect_database(database)
        exported = None
        if database_state["integrity"] == ["ok"]:
            admin_problems, exported = check_administrator_view(config, oracle)
            problems += admin_problems
        problems += oracle.mismatches

    started = args.processes * args.threads * args.journeys
    delay = {"requested_us": args.delay_us}
    requests = sum(result["requests"] for result in results)
    report = {
        "processes": args.processes, "threads": args.threads,
        "journeys_per_thread": args.journeys, "seed": args.seed,
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "journal": database_state["journal"], "delay": delay,
        "requests": requests, "seconds": round(seconds, 1),
        "requests_per_second": round(requests / max(seconds, 0.001), 1),
        "slowest_ms": {name: max(result["slowest_ms"].get(name, 0.0) for result in results)
                       for name in ("submit", "page") if results},
        "checks": judge(args, started, results, oracle, database_state, exported, delay),
        "problems": problems,
        "process_results": [{key: value for key, value in result.items() if key != "export_rows"}
                            for result in results],
    }
    for line in format_report(report):
        print(line)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as handle:
            handle.write("\n".join(format_markdown(report)) + "\n")
    return 1 if problems or any(not passed for _n, passed, _m in report["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
