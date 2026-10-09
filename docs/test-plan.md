# Test plan for release 0.7.0 (Unit 7: system testing)

This plan says what is tested before release 0.7.0, at which level, with which cases, in which environment, and what must be true before testing counts as finished. The results are in [`docs/test-report.md`](test-report.md).

## 1. Objectives

1. **Integration.** Show that the eight modules work together through their real interfaces: forms and routes, routes and the security layer, modules and the database, the jobs service and open sessions, content files and templates, and a database that an earlier release wrote.
2. **System.** Show that the complete, deployed system behaves correctly for the people who use it, including in the situations that single modules cannot show: a phone, a closed browser, two tabs, hostile requests, simultaneous use, a crash, and an upgrade.
3. **Acceptance.** Show, requirement by requirement, that the system does what the Unit 3 baseline asks, and decide objective O2: "100% of critical acceptance tests passing".
4. **Find defects** before participants do, record each one, correct it with a regression test, and run the affected cases again.

## 2. Scope

| In scope | Out of scope |
|---|---|
| The application of release 0.7.0 as deployed: three containers behind HTTPS (`docs/deployment.md`) | The pilot with participants: learning gain, completion rate, and System Usability Scale score need people (objectives O3 to O5) |
| All eleven functional requirements (FR-01 to FR-11) | Testing with a screen reader or by people who rely on assistive technology |
| The non-functional requirements that software can check: performance, accessibility checks, portability, reliability, scalability, security, privacy, retention (NFR-01, NFR-03 to NFR-12) | A penetration test by an independent party |
| The operator's procedures: installation, backup, restore, upgrade, rollback | The pilot host itself, which does not exist yet; the stack is tested on hosted CI runners |
| The six findings that release 0.6.0 left open (U-1 to U-5 and S-8) | Behaviour over weeks: the longest run is ten minutes |

## 3. Levels of testing

| Level | Question it answers | Technique | Where it runs | Cases |
|---|---|---|---|---|
| Unit and component (Unit 5 and 6) | Does each module do its job? | White-box tests of the scoring functions; black-box tests of each route, command, and job | In the Python process, with pytest; CI job 1 on Python 3.11 and 3.13 | 233 tests in `tests/` |
| **Integration** | Do the modules work together? | The real application against a real SQLite file, nothing replaced by a stand-in; every route enumerated from the application's own route table; real threads for concurrency; schema files of earlier releases | In the Python process; CI job 1 | IT-01 to IT-14 in `tests/test_integration.py` |
| **System** | Does the deployed system work as a whole, also when things go wrong? | Black-box: a real browser and plain HTTPS against the running stack; an independent oracle computes what each scripted participant must see; counts are read from the administrator's dashboard, never from the database | The Compose stack; CI jobs 2 to 5 | ST-01 to ST-18 in `system_tests/test_system.py`; ST-19 to ST-24 are procedures with their own tools |
| **Acceptance** | Does it do what was required? | One case for each functional requirement in the form given, when, then; one case for each open finding; the operator's commands | The Compose stack, in Chromium, Firefox, and WebKit; CI job 4 | AT-01 to AT-18 in `system_tests/test_acceptance.py` |
| Regression | Did a change break something that worked? | Every level above runs again on every push | CI, all five jobs | all of the above |

**Why the acceptance cases are not user acceptance testing.** They are written from the requirements and run by the developer and by the pipeline. No end user has judged the system. User acceptance is the pilot, where students complete the journey and answer the built-in usability survey; it needs approval and is planned after this release.

## 4. Environments

| | Developer machine | CI stack (authoritative) |
|---|---|---|
| Purpose | Fast feedback while writing cases and fixes | The record: every figure in the test report comes from here unless it says otherwise |
| System under test | The application on a local web server over HTTPS, production settings | The image that would be deployed: Caddy 2, Gunicorn (2 workers x 4 threads), SQLite, jobs service, on a GitHub-hosted runner (4 CPUs, Ubuntu 24.04) |
| Browsers | Chromium | Chromium, Firefox, WebKit |
| Data | Scripted test records only | Scripted test records only; a new, empty stack for every job |

The cases leave scripted records behind. They must never run against the database of a live study.

## 5. Entry and exit criteria

**Entry.** The build passes the quality gate (lint, unit tests with at least 90% statement coverage, Bandit, pip-audit), and the stack starts and answers its health check.

**Exit.** Testing of a release is finished when

- every critical acceptance case (AT-01 to AT-11) passes: 100%, as objective O2 requires;
- no defect of severity critical or high is open (objective O5, NFR-12);
- every case that failed has either a correction with a regression test, verified by running the case again, or an entry in the list of known limitations with a reason;
- the cases pass on the CI stack, in all three browser engines for the acceptance level.

**Severity of a defect.**

| Severity | Meaning |
|---|---|
| Critical | Records are lost or disclosed, or the system cannot be used |
| High | A requirement cannot be met, and there is no workaround |
| Medium | A requirement is met only with a workaround, or the system fails under input that an attacker or an unlucky user can produce |
| Low | Cosmetic, or unlikely and without effect on results |

**Handling a defect.** It gets a number (D-n) and an entry in the test report. It is corrected on a `fix/` branch together with a test that fails before the correction and passes after it. The branch is merged into `develop` when the pipeline passes, and the cases that had failed are run again (the second test cycle).

**A failing case while its defect is open.** An integration case runs in the quality gate, and a failure there stops the pipeline before the stack is built. A case that fails because of a recorded defect is therefore marked with the defect's number (`@known_defect("D-4")`). It still runs and is still reported as failed, but the pipeline goes on, so that the system and acceptance levels can be run in the same cycle. The marker cannot outlive the defect: once the case passes, the run fails until the marker is removed. System and acceptance cases are not marked, because their job is the last one and stops nothing.

## 6. Test cases

Each case names the requirement it verifies. Critical acceptance cases are marked with an asterisk.

### 6.1 Integration (in the process, `tests/test_integration.py`)

| Case | Requirement | What is checked |
|---|---|---|
| IT-01 | NFR-09 | Every state-changing route, taken from the application's route table, refuses a request without a valid anti-forgery token, and nothing is stored |
| IT-02 | FR-01, NFR-10 | Every route belongs to exactly one role (visitor, participant, administrator), and each role is refused on the other roles' routes |
| IT-03 | NFR-08, NFR-09 | Thirty pages across the three roles, error pages included, carry the security headers, are never cached, and set only hardened cookies |
| IT-04 | NFR-09 | Fifteen hostile values in each of 26 form fields are refused without a server error and without a stored record |
| IT-05 | FR-08, FR-11 | For one cohort, the results pages, the dashboard, the export, and the command-line report agree with the stored answers |
| IT-06 | FR-02, FR-10 | A second consent in a live session creates no second record |
| IT-07 | NFR-05 | Twelve simultaneous copies of an answer, eight of a final answer, and twenty simultaneous consents are each stored once, with forms still balanced |
| IT-08 | NFR-12 | The retention job deletes an expired participant who has a form open; that session ends without an error, and other sessions continue |
| IT-09 | NFR-05 | A backup taken in the middle of a session restores to that moment, and the same browser continues from there |
| IT-10 | NFR-11 | A failed sign-in stores nothing that was typed; the limit of five attempts still works; the daily job removes old attempts |
| IT-11 | FR-04 to FR-06 | All 30 scenarios and six lessons reach the screen through the real routes, in both form orders, and no assessment item shows its explanation |
| IT-12 | NFR-02, NFR-09 | Six kinds of error (400, 403, 404, 405, 413, 500) are answered by the application's own page, never in the framework's words |
| IT-13 | NFR-05, NFR-07 | A database with the schema of release 0.5.1 serves a returning participant and a new administrator after the upgrade |
| IT-14 | NFR-05, NFR-07 | A database with the schema of release 0.6.0 keeps its records and its administrator account after the upgrade |

### 6.2 System (against the running stack)

| Case | Requirement | What is checked |
|---|---|---|
| ST-01 | FR-01 to FR-10 | A complete journey in a desktop browser; scores, gain, and cue percentages equal the oracle's; the dashboard counts rise by one |
| ST-02 | NFR-04 | The same journey on a 360 px touch screen; no screen scrolls sideways |
| ST-03 | NFR-04, NFR-05 | The journey completes with JavaScript switched off |
| ST-04 | NFR-05 | The browser is closed during the pre-assessment; reopened, it resumes at the next item, and no answer is lost |
| ST-05 | NFR-05 | A double click on the consent form and on an answer stores one record, with and without JavaScript |
| ST-06 | NFR-05 | One item open in two tabs is answered once; the first answer counts |
| ST-07 | NFR-05 | The Back button and a reload neither repeat nor lose an answer |
| ST-08 | FR-03 to FR-09 | Typing the address of a later step leads back to the open step, at four stages of the journey |
| ST-09 | FR-10, NFR-08 | After Finish, the Back button, typed addresses, and a copy of the cookie show nothing; the next person gets a new identifier |
| ST-10 | FR-11, NFR-10 | The researcher signs in, reads the statistics, downloads the export, and signs out; Back shows nothing |
| ST-11 | NFR-08, NFR-11 | Every address reachable by links in every state is served with the security headers, the right caching rule, a hardened cookie, and no identifier |
| ST-12 | NFR-09 | About fifty hostile requests (values, methods, bodies, paths, cookies) are refused without a server error, and a form of 200 kB is refused for its size |
| ST-13 | FR-11, NFR-10 | Visitor, participant, and researcher cannot open each other's pages; a withdrawn participant's cookie opens nothing |
| ST-14 | NFR-10 | The sixth failed sign-in is refused; an unknown name and a wrong password get the same answer after the same work |
| ST-15 | NFR-12 | The health endpoint reports status, release, and scenario count, sets no cookie, and reveals nothing else |
| ST-16 | NFR-05, NFR-06 | Twenty-five simultaneous journeys: every score, gain, and export row is correct |
| ST-17 | NFR-05 | The web service is killed during a journey; after the automatic restart the same browser continues, and no answer is lost |
| ST-18 | NFR-05, NFR-12 | Encrypted backups written while five participants answer do not disturb them |
| ST-19 | NFR-01, NFR-06 | Load test: 455 journeys at 1 to 200 concurrent participants; 95th percentile within 500 ms (answers) and 2,000 ms (pages) at 25; every reported value correct (`evaluation/loadtest.py`) |
| ST-20 | NFR-05 | Endurance test: 25 participants at a human pace for ten minutes; no failed request, no wrong value, no slowdown, no memory growth (`evaluation/soaktest.py`) |
| ST-21 | NFR-01, NFR-03, NFR-04 | Browser audit: axe-core and scripted WCAG checks on 21 screens at five widths, a keyboard-only journey, page load on a slow connection, the journey in three engines (`evaluation/browser_audit.py`) |
| ST-22 | NFR-08, NFR-12 | Deployment checks: the image holds only the pinned packages, refuses to start without a key, serves TLS 1.2 and 1.3 only, and runs unprivileged and read-only (CI job 2) |
| ST-23 | NFR-05 | Restore rehearsal: the newest encrypted backup is restored on the running stack, and the outage is measured (CI job 2) |
| ST-24 | NFR-05, NFR-07 | Upgrade rehearsal: from the previous release to this build, back, and forward again; statistics unchanged each time (`system_tests/upgrade_rehearsal.sh`) |

### 6.3 Acceptance (against the running stack, in three browser engines)

| Case | Requirement | Given, when, then |
|---|---|---|
| AT-01 * | FR-01 | Given a visitor, when they open the site, then they see what taking part means and can reach nothing else; a form without the age confirmation is refused; declining stores nothing; agreeing opens the learning path and creates one record |
| AT-02 * | FR-02 | Given two participants, when they complete the journey, then each is known by a random identifier only, and no screen offers a field to type into |
| AT-03 * | FR-03 | Given a consenting participant, when they take the pre-assessment, then it has twelve fictional items, two for each cue and half of them phishing, shows no feedback, and ends with the score |
| AT-04 * | FR-04 | Given a finished pre-assessment, when the participant opens the lessons, then six lessons cover the six cues, each with what to look for, an example, and a safe action |
| AT-05 * | FR-05 | Given the lessons, when the participant practises, then six scenarios show emails and web pages on reserved domains with nothing that can be clicked or typed into |
| AT-06 * | FR-06 | Given a practice decision, right or wrong, then the next screen names the verdict, the cue, the reasons, and the safe action, and reveals the real link destination |
| AT-07 * | FR-07 | Given finished practice, when the participant takes the post-assessment, then it is the other form: twelve items never seen before, built to the same plan |
| AT-08 * | FR-08 | Given both assessments, then the results page shows both scores, the gain, the percentage for each cue, and the cues still to practise |
| AT-09 * | FR-09 | Given the results, when the participant answers the survey, then it shows the ten SUS statements, keeps the answers if one is missing, and stores the standard score |
| AT-10 * | FR-10 | Given a participant at any of four stages, when they withdraw and confirm, then every record is gone at once, and the session cannot be reopened |
| AT-11 * | FR-11 | Given the researcher, then nothing is shown without sign-in; statistics and export appear only from five completed participants; the export has no identifier and no time, and its row order says nothing |
| AT-12 | NFR-02 (U-1) | Given a session that has ended, when the participant sends a form or follows a link, then a page says that the session has ended and why |
| AT-13 | NFR-02 (U-2) | Given a configured contact line, then every screen of the journey shows it |
| AT-14 | NFR-02 (U-3) | Given a 360 px screen, then each survey scale stands in one row, and the page is at most 2,600 px long |
| AT-15 | NFR-02 (U-4) | Given an unknown address, then the answer is a page in plain words without a code as its headline, and the way back keeps the session |
| AT-16 | NFR-04 (U-5) | Given the six feedback screens at 320, 360, and 412 px, then no bracket stands apart from the address it encloses |
| AT-17 | NFR-10 (S-8) | Given the operator, when they choose a commonly used password for the administrator, then it is refused, in any capitalisation |
| AT-18 | NFR-12 | Given the operator, then the documented commands (statistics, backup, retention dry run, account creation) work on the running instance |

## 7. Requirements and the cases that verify them

| Requirement | Cases |
|---|---|
| FR-01 Consent before storage | AT-01, IT-02, ST-01 |
| FR-02 Random identifier only | AT-02, IT-06, ST-11 |
| FR-03 Pre-assessment | AT-03, ST-08 |
| FR-04 Lessons | AT-04, IT-11 |
| FR-05 Practice scenarios | AT-05, IT-11 |
| FR-06 Immediate feedback | AT-06, IT-11 |
| FR-07 Parallel post-assessment | AT-07, ST-08 |
| FR-08 Scores and cue feedback | AT-08, IT-05, ST-01, ST-16, ST-19 |
| FR-09 Usability survey | AT-09, ST-08 |
| FR-10 Withdrawal | AT-10, ST-09, ST-13 |
| FR-11 Aggregate reporting | AT-11, IT-05, ST-10, ST-13 |
| NFR-01 Performance | ST-19, ST-20, ST-21 |
| NFR-02 Usability | AT-12 to AT-15, IT-12; the usability score itself needs participants |
| NFR-03 Accessibility | ST-21 |
| NFR-04 Portability | ST-02, ST-03, ST-21, AT-16; AT-01 to AT-18 in three engines |
| NFR-05 Reliability | IT-07, IT-09, ST-04 to ST-07, ST-16 to ST-18, ST-20, ST-23, ST-24 |
| NFR-06 Scalability | ST-16, ST-19 |
| NFR-07 Maintainability | IT-11, IT-13, IT-14, ST-24 |
| NFR-08 Transport and cookies | IT-03, ST-09, ST-11, ST-22 |
| NFR-09 Injection and scripting | IT-01, IT-03, IT-04, IT-12, ST-12 |
| NFR-10 Least privilege | IT-02, ST-10, ST-13, ST-14, AT-17 |
| NFR-11 Privacy | IT-10, ST-11, AT-02, AT-11 |
| NFR-12 Compliance and retention | IT-08, ST-15, ST-18, ST-22, AT-18; `docs/security-checklist.md` |

## 8. Procedures

```bash
# Unit, component, and integration tests (no server needed)
pip install -r requirements-dev.txt
coverage run -m pytest && coverage report

# The integration cases alone, as a report with case numbers
python -m system_tests.run --level integration

# System and acceptance cases against a running stack (docs/deployment.md, section 5)
pip install -r evaluation/requirements.txt && python -m playwright install chromium firefox webkit
python -m system_tests.run --level acceptance,system \
    --base-url https://localhost --cacert caddy-root.crt --ignore-https-errors \
    --admin-user evaluator --admin-password "..." \
    --cli "docker compose exec -T app flask" \
    --crash-command "sh system_tests/crash_web_service.sh"
python -m system_tests.run --level acceptance --browser firefox ...     # and webkit

# Procedures with their own tools
python evaluation/loadtest.py --base-url https://localhost --cacert caddy-root.crt \
    --levels 1,5,10,25,50,100,200 --min-journeys 25 --admin-user evaluator --admin-password "..."
python evaluation/soaktest.py --base-url https://localhost --cacert caddy-root.crt \
    --users 25 --minutes 10 --admin-user evaluator --admin-password "..." \
    --memory-command "docker compose exec -T app cat /sys/fs/cgroup/memory.current"
bash system_tests/upgrade_rehearsal.sh
```

The acceptance level runs first, on an empty database, because AT-11 can observe the reporting threshold only before five participants have finished. Options that are left out turn the cases that need them into "not run", which the report lists; `--strict` treats that as a failure, and the pipeline uses it.

**What a case may use.** A system or acceptance case sees the system only as its users do: pages, HTTP replies, the administrator's dashboard and export, and the operator's commands. It reads `data/scenarios.json` as an answer key, in order to answer rightly or wrongly on purpose, and computes the expected scores with its own arithmetic.

## 9. Reporting

`python -m system_tests.run` prints one line for each case with its verdict, a summary by level, the number of critical acceptance cases that passed, and the reason for every failure; `--json` and `--markdown` write the same report to files. The pipeline publishes each report as a notice on the run. `docs/test-report.md` is the test summary report of the release: objectives, scope, the results of both test cycles, the defects found, how each was resolved, and the limitations that remain.
