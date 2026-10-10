# Test summary report for release 0.7.0 (Unit 7: system testing)

This report says what was tested before release 0.7.0, what the tests found, how each finding was resolved, and what the tests cannot show. The plan it follows is [`docs/test-plan.md`](test-plan.md). Testing took place on 9 and 10 October 2026.

**No participant has used the system.** Every result below comes from scripted sessions, from automated checks, or from the developer's inspection. The scripted answers are test data. They say whether the software works, and nothing about whether students learn from it.

## 1. Summary

| | Result |
|---|---|
| Critical acceptance cases (AT-01 to AT-11), objective O2 | **11 of 11 passed** in Chromium, Firefox, and WebKit |
| Integration cases | 15 of 15 passed (9 of 14 in the first cycle) |
| System cases | 18 of 18 passed (17 of 18 in the first cycle) |
| Acceptance cases | 18 of 18 passed in each of three browser engines (12 of 18 in the first cycle) |
| Procedures ST-19 to ST-25 | all seven passed |
| Unit, component, and integration tests | 320 passed on Python 3.11 and 3.13; every statement of the application executed (1,502 statements) |
| Defects found | 7 (D-1 to D-7): one critical, one high, two medium, three low. All corrected, each with a regression test |
| Findings of earlier inspections closed | 7 (U-1 to U-6 and S-8) |
| Faults found in the tests themselves | 4 (T-1 to T-4), all corrected |
| Open defects of severity critical or high | none |

Release 0.7.0 meets the exit criteria of the test plan (section 9).

The most important result is defect D-7. Release 0.6.0 could lose stored answers and damage its database when two server processes wrote at the same moment. Ten measurements of that release had reported no failed request and no wrong value, and the Unit 6 report concluded from them that no answer is lost. The defect appeared when the pipeline was run again on unchanged code, about once in a million requests there. Section 6.2 describes it.

## 2. Objectives and scope

The objectives are those of the plan: show that the modules work together (integration), that the deployed system works as a whole, also when things go wrong (system), and that it does what the requirements ask (acceptance); and find defects before participants do.

In scope were the application as deployed (three containers behind HTTPS), all eleven functional requirements, the non-functional requirements that software can check, and the operator's procedures. Out of scope were the pilot with participants, testing with assistive technology, an independent penetration test, the pilot host, and behaviour over weeks. The plan gives the reasons (section 2).

## 3. What was tested, and where

| | |
|---|---|
| Test item | PhishAware 0.7.0, commit `d431dbe` on the branch `release/0.7.0` |
| Authoritative environment | The Compose stack on a GitHub-hosted runner (4 CPUs, 15.6 GB, Ubuntu 24.04.5): Caddy 2.11.7, Gunicorn 26.2.0 with 2 workers x 4 threads, Python 3.13.16, Flask 3.1.3, Werkzeug 3.1.9, SQLite 3.46.1 in journal mode `delete` |
| Developer machine | The application on a local web server over HTTPS with production settings; Python 3.13.16, SQLite 3.45.1, Chromium 141 |
| Browsers | Chromium 141.0.7390.37, Firefox 142.0.1, WebKit 26.0 (Playwright 1.56.0) |
| Tools | pytest and coverage.py; `system_tests/` (harness, 18 system cases, 18 acceptance cases, runner); `evaluation/loadtest.py`, `soaktest.py`, `browser_audit.py`, `contention.py`; axe-core 4.10.2; flake8, Bandit, pip-audit |

The cases, their requirements, and the commands are in the plan (sections 6 to 8).

## 4. Test cycles

| Cycle | Code | Pipeline runs | Purpose and outcome |
|---|---|---|---|
| 1 | Release 0.6.0 with the new cases | 35, 38 (9 October); 33 (completed on 10 October, after three attempts that could not fetch their base images) | First execution of every case. 5 integration cases, 1 system case, and 6 acceptance cases failed: six defects (D-1 to D-6) and the six findings that 0.6.0 had left open |
| 2 | All corrections of cycle 1 merged | 52, 53 (9 October) | Every case that had failed was run again, with all others. Everything passed |
| Between | The code of cycles 1 and 2, unchanged | 33 again, 54 (10 October) | The pipeline was run again without a change. The load test failed once (D-7), and one system case failed for a reason outside the application (T-2) |
| 3 | The corrections of D-7 and of the test faults merged | 55 to 61 on branches, 63 on `develop`, 64 on `release/0.7.0` (10 October) | Everything passed. During this cycle D-7 showed itself a second time, on a branch that did not yet have its correction (run 59) |

A run number is the number that GitHub Actions shows for the workflow "CI". Each run publishes its reports as notices, and the system-test job keeps its screenshots and JSON results for 90 days.

## 5. Results by level

### 5.1 Unit and component tests

| Release | Tests | Statements of `src/` | Executed |
|---|---|---|---|
| 0.6.0 | 233 | 1,280 | 100% |
| Cycle 2 (run 53) | 295 | 1,387 | 100% |
| 0.7.0 | 320 | 1,502 | 100% |

The count includes the integration cases, which run in the same process. Every correction added the tests that fail without it.

### 5.2 Integration cases (`tests/test_integration.py`)

| Cycle | Passed | Failed |
|---|---|---|
| 1 (run 35) | 9 of 14 | IT-04 (D-1), IT-06 (D-3), IT-10 (D-4), IT-12 (D-2, D-6), IT-13 (D-5) |
| 2 (runs 52, 53) | 14 of 14 | none |
| 3 | 15 of 15 | none. IT-15 is new: the web service closes no database connection while it serves requests (D-7) |

### 5.3 System cases (`system_tests/test_system.py`, Chromium, against the running stack)

| Cycle | Passed | Failed |
|---|---|---|
| 1 (run 38) | 17 of 18 | ST-12 (D-1, D-6): three hostile tokens were answered with status 500, and a form of 200 kB was not refused for its size |
| 2 (runs 52, 53) | 18 of 18 | none |
| Between (run 54) | 17 of 18 | ST-17, error in the browser, not in the application (T-2) |
| 3 | 18 of 18 | none. On branches that did not yet have the corrections of the test faults, ST-14 failed once (T-4) and ST-17 twice (T-2) |

Figures the cases reported in the third cycle (run 64): ST-01 showed 66.7% before and 91.7% after for its scripted journey, as its oracle expects. ST-11 checked 50 replies from 20 addresses, and ST-12 sent 50 hostile requests. ST-14 measured 244 ms for a wrong password and 240 ms for an unknown name (medians). ST-16 completed 25 simultaneous journeys in 3.5 s. In ST-17 the service answered again 1.2 s after it was killed, and no answer was lost. In ST-18 two backups were written during five journeys. AT-14 measured the survey page at 2,412 px in Chromium, 2,440 px in Firefox, and 2,315 px in WebKit.

### 5.4 Acceptance cases (`system_tests/test_acceptance.py`, three browser engines)

| Cycle | Chromium | Firefox | WebKit | Critical cases (AT-01 to AT-11) |
|---|---|---|---|---|
| 1 (run 38) | 12 of 18 | 12 of 18 | 7 of 18 | 11 of 11 in Chromium and Firefox; 6 of 11 in WebKit, where five cases failed because of the test itself (T-1). Without screenshots the same code had passed 11 of 11 there (run 33) |
| 2 (runs 52, 53) | 18 of 18 | 18 of 18 | 18 of 18 | 11 of 11 in all three |
| 3 | 18 of 18 | 18 of 18 | 18 of 18 | **11 of 11 in all three: 100%** |

The six cases that failed in every engine in the first cycle were AT-12 to AT-17. Each was written for a finding that release 0.6.0 had left open, so each was expected to fail until that finding was corrected (section 7).

These are acceptance tests against the requirements, run by the developer and the pipeline. No user has accepted the system: that is the pilot.

### 5.5 Procedures with their own tools (third cycle, run 64)

| Procedure | Result |
|---|---|
| ST-19 Load test | 455 journeys at 1 to 200 simultaneous participants, 38,220 requests, none failed. At 25 participants the 95th percentile was 85.1 ms for answers (limit 500) and 81.0 ms for pages (limit 2,000). 14,566 of 14,566 reported values correct. The comparison with write-ahead logging: 46.2 ms and 49.0 ms, and again 14,566 of 14,566 |
| ST-20 Endurance test | Ten minutes, 25 participants at a human pace: 497 journeys, 41,748 requests, none failed; 15,910 of 15,910 reported values correct; 95th percentile over the whole run 7.5 ms for answers and 5.2 ms for pages; no slowdown (7.3 ms in the first third, 7.5 ms in the last); memory of the web service 66.1 MiB in the middle third and 66.5 MiB in the last (ratio 1.01, limit 1.15) |
| ST-21 Browser audit | 21 screens: no violation of the axe-core rules, one rule for manual review (the contrast of chart labels, which Unit 6 checked by hand); 1,963 scripted WCAG checks, none failed; a journey by keyboard alone with 432 key presses; on an emulated slow 4G connection, submitting an answer and loading the next screen took 1,766 ms at the 95th percentile (limit 2,000); the journey completed in Firefox 142 and WebKit 26 without a layout failure |
| ST-22 Deployment checks | The image holds the 11 pinned packages and nothing else and refuses to start without a secret key; the proxy accepts TLS 1.2 and 1.3 and refuses 1.1; the server runs unprivileged on a read-only file system |
| ST-23 Restore rehearsal | Services stopped after 1 s, backup restored after 2 s, all services healthy after 14 s; the site was unavailable for at most 2.0 seconds; the change made after the backup was gone, as it must be |
| ST-24 Upgrade rehearsal | From release 0.6.0 to 0.7.0, back to 0.6.0, and forward again: healthy after 22 s each time, with the statistics unchanged; the administrator account of the earlier release signed in, and a new journey completed |
| ST-25 Contention test | Two processes with four threads each, `close()` on the database file delayed by 0.3 ms: 200 of 200 journeys, 16,800 requests, no server error, 6,406 of 6,406 reported values correct, integrity check ok, 8 of 8 connections still open; on Python 3.11 and on 3.13 |

**An observation without an explanation.** Run 63 tested the same application code on `develop` a few minutes earlier. Everything passed there too, but three figures were unlike those of run 64: the 95th percentile of answers in the endurance test was 236 ms (7.5 ms in run 64), ST-16 took 8.7 s (3.5 s), and in the comparison with write-ahead logging one answer at 50 participants waited 5.07 s for the write lock. Each was inside its limit. The three jobs ran on three different machines at the same time, so one slow machine does not explain it, and the cause was not found. One consequence can be stated: up to release 0.6.0 a statement gave up after 5 seconds, so that one answer would probably have been refused with an error page; it now waits up to 15.

## 6. Defects

### 6.1 Overview

Severity follows the scale of the plan (section 5). Each correction is a `fix/` branch with a test that fails before it and passes after it.

| ID | Severity | Found by | Defect | Correction |
|---|---|---|---|---|
| D-1 | Medium | IT-04, ST-12 | A form whose anti-forgery token held a character outside ASCII was answered with status 500, on every form | The two tokens are compared as bytes; such a token is refused with status 400 like any other wrong token |
| D-2 | Low | IT-12, AT-15 | Only five status codes had the application's own error page; any other HTTP error got the framework's page, and the error page needed the database, so it failed when the database was the cause | One handler for every HTTP error, in the application's words; a plain page when the database cannot be read |
| D-3 | Low | IT-06 | A consent form sent in a live session created a second participant record that no browser could continue or withdraw | Such a request is redirected to the dashboard, and nothing is stored |
| D-4 | Low | IT-10 | A failed sign-in stored the user name as typed, which may be a password typed into the wrong field, and the record was removed only by a later sign-in | A keyed digest (HMAC-SHA-256) is stored in its place; the daily pass deletes records older than 15 minutes |
| D-5 | High for an installation that began with 0.4.0 to 0.5.1 (none exists) | IT-13 | After the upgrade to 0.6.0, such a database could not hold an administrator ("no such column: session_stamp"), so the results could not be read | Migration steps in one transaction; the database records its schema version; tests build databases from the schema files of the earlier releases |
| D-6 | Medium | ST-12, IT-12 | No limit on the size of a request since Werkzeug 3.1.9: a form of 50 MB was read in full and cost 150 MiB of memory | A limit of 64 kB; a larger request is refused with status 413 before it is read |
| D-7 | **Critical** | ST-19, repeated | Two server processes could write at the same moment: a request failed with status 500, stored answers were overwritten, and the database could be damaged | The web service keeps its database connections open in a pool for each process; section 6.2 |

### 6.2 Defect D-7: two writers at the same moment

**How it showed.** On 10 October the pipeline was run again on unchanged code (run 33, fifth attempt; the application code is that of release 0.6.0). The load test, which had passed on this code in every earlier run, failed at 200 simultaneous participants: two requests were answered with status 500, 453 of 455 journeys were completed, and 14,499 of 14,502 reported values matched the oracle. The three that did not match were counts on the administrator's dashboard: records were missing.

Before that run, the load test had passed in all ten measurements of release 0.6.0 that Unit 6 reported and in 19 runs of this unit, 38,220 requests each. Counting those, the defect showed itself once in about 1.1 million requests.

It showed itself a second time during the third cycle, on a branch that did not yet contain the correction (run 59), this time with write-ahead logging: at 200 simultaneous participants the application logged `sqlite3.DatabaseError: database disk image is malformed`, 359 requests failed, and 49 of 200 journeys were completed. The database of that run was damaged.

**Reproduction.** Two processes with four threads each, as the service is deployed, took complete journeys through the application on a developer machine, without Gunicorn, Docker, or a network. In two of three runs of about 100,000 requests, one request failed:

```
sqlite3.OperationalError: disk I/O error        (SQLITE_IOERR_DELETE_NOENT)
```

In one of those runs, 15 figures of the dashboard and the export then differed from the oracle: answers that other participants had already stored were gone. One process with eight threads did not fail, and eight processes with one thread each did not fail.

**Cause.** SQLite locks the database file with POSIX advisory locks. Such a lock belongs to the process: when the process closes any descriptor of the file, the operating system releases all its locks on that file. SQLite's documentation describes this property and says that the library works around it ([How To Corrupt An SQLite Database File](https://www.sqlite.org/howtocorrupt.html), section 2.2). The work-around is this: before SQLite closes a database file, it checks whether another connection of the process holds a lock, and postpones the close if one does. In the release that the image ships (3.46.1), the check and the `close()` are two steps, and a lock is taken under a different mutex. A lock that another thread takes between the two steps is released by the `close()`, and neither SQLite nor that thread notices. The current source of SQLite has the same two steps.

Up to release 0.6.0 every request opened a database connection and closed it at its end, in eight threads of two processes. A system-call trace of the unchanged application shows the sequence. A thread of process A holds the write lock in the middle of a transaction. Another thread of process A closes its connection. Process B asks whether the write lock is taken, is told that it is not, begins to write, and opens the same rollback journal. A deletes the journal when it commits. B's commit then fails because the journal is gone, and whatever B wrote into pages that A also wrote is lost.

How often this happens depends only on timing. A small library that makes `close()` on the database file take 0.3 ms longer and changes nothing else (`evaluation/slowclose.c`) turns the rare fault into a certain one:

| | Release 0.6.0 | Release 0.7.0 |
|---|---|---|
| Journeys completed | 12 of 200 | 200 of 200 |
| Requests answered with a server error | 188 of 4,235 | 0 of 16,800 |
| SQLite's integrity check afterwards | failed: "wrong # of entries in index" | ok |
| Reported values that match the oracle | not comparable: the dashboard could not be read | 6,406 of 6,406 |

**Correction.** The web service never closes a database connection while it serves requests. Each process keeps its connections in a pool (`src/db.py`, `ConnectionPool`): a request borrows one and returns it, after the cursors it left open are closed and a transaction it left open is rolled back. The pool grows to the number of requests the process has had in progress at one moment, which is its number of threads. A command, the start-up, and the jobs service still open and close their own connection; each of them is a process with one thread, so no thread of its own can be in between. A statement now waits 15 seconds for a lock, not 5, before it fails.

**Verification.** Three levels of test now cover it. Sixteen unit tests check the rules of the pool (`tests/test_connections.py`); those that state the rule itself, that no connection is closed while requests are served, fail on the earlier code. Integration case IT-15 checks that eight simultaneous participants are served without a connection being closed. Procedure ST-25, the contention test with the delayed `close()`, runs in the quality gate on every push, on both Python versions. On the corrected code it also passed with three processes, in write-ahead mode, without the delay, and in a run of 67,200 requests.

**What it would have meant.** In a pilot of 20 to 50 participants the service handles a few thousand requests in all, so the fault would most probably never have occurred. If it had, a participant would have seen an error page, another participant's stored answers could have been lost without any sign, and in the worst case the database would have needed a restore. No participant data was affected, because the pilot has not started.

**What followed from it.** The daily backup would have copied a damaged database without a word, and after seven days no sound backup would have been left. The maintenance pass now checks the snapshot with SQLite's integrity check and foreign-key check before it encrypts it. A damaged database is reported and not backed up, and the earlier backups are kept for as long as the damage lasts.

## 7. Findings of earlier inspections that this release closes

| ID | Finding in release 0.6.0 | Case | Resolution |
|---|---|---|---|
| U-1 | A session that had expired led back to the consent page without a word, or to advice that went in a circle | AT-12 | A notice on the consent page and a page "Your session has ended" say what happened and why |
| U-2 | The researcher's contact line was on the consent page only | AT-13 | It stands at the foot of every page |
| U-3 | The survey was 3,941 px long on a 360 px screen | AT-14 | Each scale stands in one row; the page is 2,315 to 2,440 px long in the three engines |
| U-4 | The page for an unknown address spoke in the framework's words | AT-15 | The application's own page, in plain words (with D-2) |
| U-5 | An opening bracket could be separated from the address it encloses | AT-16 | Punctuation that touches an address stays with it |
| U-6 | The dashboard did not say how many items of an interrupted step were answered (found while ST-04 was written) | ST-04 | The count stands under the progress bar |
| S-8 | Any password of twelve characters was accepted for the administrator (OWASP ASVS 6.2.4) | AT-17 | A commonly used password is refused, in any capitalisation |

## 8. Faults in the tests

A failing case is not always a failing application. Four times the test was at fault. Each time the cause was found before anything was changed, and the correction keeps what the case is for.

| ID | Case | What happened | Correction |
|---|---|---|---|
| T-1 | AT-01, AT-03, AT-04, AT-06, AT-08 in WebKit (runs 38, 40) | Before a screenshot, Playwright adds a style element to the page in WebKit. The application's content security policy refused it, as it should, and the cases counted the browser's message as a problem of the page | The harness drops this one message if it arrives during a screenshot. The first correction rested on a wrong diagnosis and did not work (run 40) |
| T-2 | ST-17 (runs 54, 55, 61) | After the web service was killed and restarted, Chromium ended a request with `net::ERR_NETWORK_CHANGED`: the restarted container had rejoined the host's network. The request never reached the application | The case waits three seconds after the health check answers, and reloads again if the browser reports a network error of its own |
| T-3 | ST-20 (runs 53, 57, 60) | The "no leak" check compared the memory of the web service in the last third of the run with the first third and passed or failed by chance: the ratio lay between 1.13 and 1.34 in five runs of code without a leak, against a limit of 1.25. It measured the server's warm-up, not a leak | The check compares the last third with the middle third and is judged only in the ten-minute run. See below |
| T-4 | ST-14 (run 55) | The case compared the fastest of four replies with the slowest of two, and one reply on a shared runner took 358 ms where the others took about 135 ms | The case compares the medians |

**T-3 in more detail, because the first explanation was wrong.** The rising figure was first taken for the kernel's file cache, and the test was changed to read the memory of the processes alone. The same change made the pipeline print what the total consists of, and the next run showed that almost all of it is the processes' own memory (73.9 of 77.9 MB). Two ten-minute runs then showed the cause. Gunicorn loads the application once and forks its workers, which share that memory until they first write to a page; Python's garbage collector writes a mark into every object it examines, so each worker ends up with a copy of nearly everything. That rise ends: one process without a fork served 88,032 requests with 27.9 to 30.9 MiB. The server now freezes the loaded objects before it forks (`gc.freeze` in `gunicorn.conf.py`), which the collector then leaves alone:

| Minute | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Without the hook (run 60), MiB | 51.7 | 58.0 | 62.9 | 70.6 | 76.7 | 76.8 | 76.8 | 76.9 | 79.4 | 80.7 |
| With the hook (run 61), MiB | 54.2 | 57.0 | 58.6 | 60.3 | 64.7 | 64.8 | 64.9 | 65.3 | 65.8 | 65.8 |

**Failures of the infrastructure.** Three kinds of failure had nothing to do with the code or the tests: Docker Hub refused anonymous downloads from the shared runners with status 429 (runs 33 and 34; the pipeline now fetches its base images from two mirrors first), the step that installs the browsers ran into its time limit (run 43), and a cancelled run (48).

## 9. Exit criteria

| Criterion of the plan | Met? |
|---|---|
| Every critical acceptance case passes | Yes: 11 of 11 in three browser engines |
| No defect of severity critical or high is open | Yes: D-7 and D-5 are corrected and verified |
| Every case that failed has a correction with a regression test, or an entry in the known limitations | Yes: sections 6 to 8 |
| The cases pass on the CI stack, the acceptance level in all three engines | Yes: run 64 |

## 10. What this testing does not show

- **Whether students learn.** No participant has used the system. Objectives O3 to O5 and the three research questions are open.
- **The absence of rare faults.** D-7 was invisible in 29 load tests. The contention test now makes that one fault certain, but only that one: it delays one system call on one file. Another fault of the same rarity would again need luck, or another idea, to be found.
- **Independence.** One person wrote the code, the cases, and the oracle. The oracle is written separately from the application, but the same reading of the requirements stands behind both.
- **The pilot host.** Every figure comes from hosted runners with four processors, where the load generator runs beside the stack. Response times differ between such runners (Unit 6 found more than tenfold), and the host of a pilot does not exist yet.
- **Time.** The longest run is ten minutes. The memory check can find a leak that adds more than 15% to the web service's memory within a third of that run, and no slower one.
- **Waiting for the write lock.** SQLite admits one writer at a time, and a statement that has to wait asks again at growing intervals, so those that arrive later can overtake it. Under a load far above that of a pilot, a single answer can therefore wait for seconds: 5.07 s once in run 63, and up to 1.3 s in run 64.
- **Assistive technology.** The accessibility checks are automated. Nobody who relies on a screen reader has tried the system.
- **SQLite itself.** The correction avoids the conditions of D-7 in the application. It does not change SQLite, and the application depends on never closing a connection in a web process that serves requests.
- **The pipeline is not fully dependable.** Two of the four test faults came from timing on shared machines, and such faults can recur in other cases.

## 11. Evidence

| Evidence | Where |
|---|---|
| Reports of every run: tests, coverage, load test, endurance test, browser audit, system and acceptance cases, rehearsals | Notices on each run of the workflow "CI" in the repository's Actions page |
| Screenshots of the screens the acceptance cases looked at, and the full results as JSON | Artifact `system-test-evidence` of each run (kept for 90 days) |
| The cases | `tests/test_integration.py`, `system_tests/`, `evaluation/` |
| Each defect, its cause, and its correction | The commit message of its `fix/` branch (`git log --grep "D-7"`) |
| The measurements of release 0.6.0 | `docs/evaluation.md` |
