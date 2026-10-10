# Evaluation of PhishAware 0.6.0

This document reports how release 0.6.0 was measured and what the measurements show. It uses two kinds of evidence: quantitative metrics, which a tool counts or times, and qualitative assessment, which rests on an evaluator's judgement against published criteria.

**No participant has used the system yet.** Every figure below comes from scripted sessions or from inspection. The figures describe the software. They say nothing about whether students learn from it; the pilot answers that question (RQ1 to RQ3), and it has not started.

**Correction, 10 October 2026.** This document remains the record of what was measured for release 0.6.0, and its figures are unchanged. One of its conclusions does not hold. Section 2.3 says that no answer was lost under load, and section 4 counts NFR-05 as met. System testing for release 0.7.0 found that release 0.6.0 could lose stored answers and damage its database when two server processes wrote at the same moment (defect D-7). None of the ten measurements below met the fault: in the pipeline it showed itself about once in a million requests, and these measurements made 382,200. Release 0.7.0 corrects it. [`docs/test-report.md`](test-report.md) reports the defect, its cause, the correction, and the measurements of release 0.7.0.

## 1. Method

| | |
|---|---|
| System under test | The stack that `docs/deployment.md` describes: Caddy 2 for HTTPS, Gunicorn with 2 worker processes x 4 threads, and SQLite in journal mode `delete`, built from commit `04f8f1b` (release candidate 0.6.0) |
| Software in the image | Python 3.13.16, Flask 3.1.3, Werkzeug 3.1.9, Gunicorn 26.2.0, SQLite 3.46.1 |
| Test machine | A GitHub-hosted runner: 4 CPUs, 15.6 GB of memory, Ubuntu 24.04.5 LTS, kernel 6.17.0-1022-azure; Docker 28.0.4, Compose 2.38.2 |
| Run reported | CI run 26 of 8 October 2026 (UTC). The tables in sections 2.1 to 2.6 report this run. The pipeline measured the same application code ten times that evening (runs 24 to 32, one of them twice); section 2.7 compares the measurements |
| Load test | `evaluation/loadtest.py`: complete participant journeys over HTTPS at 1, 5, 10, 25, 50, 100, and 200 concurrent participants, 84 requests per journey, no pause between requests |
| Browser audit | `evaluation/browser_audit.py`: Chromium 141.0.7390.37 with axe-core 4.10.2; the journey is repeated in Firefox and WebKit |
| Usability inspection | A heuristic evaluation of all 21 screens at 360 px and 1,280 px against Nielsen's ten usability heuristics, with his severity scale from 0 to 4 |

The CI pipeline (`.github/workflows/ci.yml`, job "Evaluate the running stack") repeats the load test and the browser audit on every push and publishes the reports as notices on the run. The job fails when a budget of NFR-01 is exceeded, when a reported score is wrong, or when an accessibility check fails.

The load generator simulates a participant who never pauses to read. Twenty-five such participants send far more requests per second than twenty-five people would, so each level is harder than the same number of real participants.

## 2. Quantitative results

### 2.1 Response time under load (NFR-01)

NFR-01 requires that an answer is processed within 500 ms and a page is delivered within 2,000 ms for 25 concurrent users. Times are measured at the client and include HTTPS and the reverse proxy.

| Concurrent participants | Answers: median (ms) | Answers: 95th percentile (ms) | Answers: slowest (ms) | Pages: median (ms) | Pages: 95th percentile (ms) | Answers within 500 ms | Pages within 2,000 ms |
|---|---|---|---|---|---|---|---|
| 1 | 2.8 | 3.6 | 61.0 | 2.4 | 3.9 | 800 of 800 (100.0%) | 1,125 of 1,125 (100.0%) |
| 5 | 6.8 | 23.9 | 112.4 | 6.1 | 21.8 | 800 of 800 (100.0%) | 1,125 of 1,125 (100.0%) |
| 10 | 14.9 | 47.9 | 189.7 | 14.4 | 43.4 | 960 of 960 (100.0%) | 1,350 of 1,350 (100.0%) |
| 25 | 38.1 | 99.3 | 256.7 | 39.7 | 96.7 | 800 of 800 (100.0%) | 1,125 of 1,125 (100.0%) |
| 50 | 88.4 | 152.2 | 735.9 | 89.5 | 166.6 | 1,598 of 1,600 (99.9%) | 2,250 of 2,250 (100.0%) |
| 100 | 180.0 | 252.2 | 500.0 | 179.3 | 309.1 | 3,199 of 3,200 (100.0%) | 4,500 of 4,500 (100.0%) |
| 200 | 358.9 | 449.7 | 1,565.5 | 356.2 | 657.7 | 6,324 of 6,400 (98.8%) | 9,000 of 9,000 (100.0%) |

At the required load of 25 participants, 95% of answers were processed within 99.3 ms and 95% of pages were delivered within 96.7 ms. In this run all 800 answers and all 1,125 page loads at that level stayed inside their limits. Other measurements of the same code gave 79.4 to 107.5 ms for answers on runners of the same speed, and several times more on two slower runners (section 2.7).

A percentile does not show how often a single request is slow, so the table also counts the requests inside each limit. In this run the first answer slower than 500 ms appeared at 50 participants. At 200 participants, eight times the required load, 6,324 of 6,400 answers (98.8%) were inside the limit and the 95th percentile was 449.7 ms. No page load exceeded 2,000 ms at any level. The slowest requests differ between measurements more than the percentiles do; section 2.7 reports them for every measurement.

### 2.2 Throughput (NFR-06)

| Concurrent participants | Journeys | Requests | Failed | Requests per second | Journeys per minute | Requests per second with write-ahead logging |
|---|---|---|---|---|---|---|
| 1 | 25 | 2,100 | 0 | 367.0 | 262.2 | 332.7 |
| 5 | 25 | 2,100 | 0 | 593.6 | 424.0 | 748.3 |
| 10 | 30 | 2,520 | 0 | 576.9 | 412.1 | 775.4 |
| 25 | 25 | 2,100 | 0 | 540.2 | 385.9 | 772.1 |
| 50 | 50 | 4,200 | 0 | 557.4 | 398.1 | 769.2 |
| 100 | 100 | 8,400 | 0 | 557.2 | 398.0 | 765.5 |
| 200 | 200 | 16,800 | 0 | 558.0 | 398.6 | 772.9 |
| Total | 455 | 38,220 | 0 | | | |

From 5 participants upward the stack served between 540 and 594 requests per second, and none of the 38,220 requests failed. Throughput levels off from 5 participants: the server processes at most eight requests at once (two workers with four threads each), every answer is a database write, and SQLite admits one writer at a time. Additional participants therefore wait longer, which is why the response times in section 2.1 grow with load while throughput stays level. NFR-06 asks for 50 pilot users; the stack completed 200 concurrent journeys without an error.

**Journal mode.** The image ships SQLite 3.46.1, which has the WAL-reset bug that SQLite's documentation describes, so the default setting `AUTO` selects the rollback journal. The last column repeats the test with write-ahead logging forced on: 748 to 775 requests per second, about 36% more, and a 95th percentile of 47.0 ms for answers at 25 participants instead of 99.3 ms. In the eight measurements on runners of normal speed (section 2.7) the gain ranged from 32% to 92%. Write-ahead logging also removed most of the slow answers there: 8 of 116,480 answers took longer than 500 ms over all levels, all at 200 participants, against 1,463 of 116,480 with the rollback journal. The cautious default costs throughput and slow answers under heavy load, and on those runners it still met the budgets of NFR-01 with a wide margin, so it stays. `AUTO` will select write-ahead logging by itself once the image contains a fixed SQLite.

### 2.3 Accuracy of reported results (FR-08, FR-09, FR-11)

The load test scripts every answer from a fixed seed, so it knows what each score must be. It computes the expected values with its own code, written independently of the application, and compares them with what the system shows. **14,566 of 14,566 reported values matched**: 455 journeys x 15 results-page checks; 455 export rows x 17 values; 5 dashboard counts; 1 row count. The check covers the personal results page (both scores, the gain, and the percentage correct for each cue before and after), the administrator's dashboard, and every value in the de-identified export, including each System Usability Scale score.

The result holds under load: the values were produced while up to 200 journeys ran at once, so no answer was lost, duplicated, or attributed to another participant. (In these measurements. See the correction at the top of this document: the release could lose answers, and none of these runs met the fault.)

### 2.4 Memory and storage

| Container | Peak memory during the load test | Memory afterwards | Processes |
|---|---|---|---|
| `app` | 113.4 MiB | 87.1 MiB | 12 |
| `jobs` | 53.0 MiB | 28.8 MiB | 2 |
| `proxy` | 65.9 MiB | 61.3 MiB | 11 |

The three containers peaked at 232 MiB together, well inside the 1 GB of memory that the deployment guide asks for. Peak memory is read from each container's control group (`memory.peak`). After 455 complete journeys the database file was 1.76 MB, about 3.5 kB per participant beyond the scenario bank. The image is 142.3 MB.

### 2.5 Page load in the browser (NFR-01)

The load test times requests. A participant experiences something else: the time until the page has loaded with its style sheet and script. The audit reads that time from the browser's own Navigation Timing record for every screen of a complete journey, once without throttling and once with the network and processor limits that Lighthouse uses for its slow 4G profile (562.5 ms added to every request and a processor slowed four times).

| Condition | Navigation | Count | Median (ms) | 95th percentile (ms) | Slowest (ms) |
|---|---|---|---|---|---|
| No throttling | Open a screen | 17 | 17 | 31 | 31 |
| No throttling | Submit to next screen | 35 | 23 | 26 | 27 |
| No throttling | All navigations | 53 | 22 | 27 | 31 |
| Emulated slow 4G | Open a screen | 17 | 612 | 635 | 635 |
| Emulated slow 4G | Submit to next screen | 35 | 1,198 | 1,759 | 1,763 |
| Emulated slow 4G | All navigations | 53 | 1,188 | 1,231 | 1,763 |

Without throttling, 95% of navigations finished within 27 ms. On the emulated slow connection, opening a screen took 612 ms at the median, and submitting a form and receiving the next screen took 1,198 ms at the median and 1,759 ms at the 95th percentile (1,741 to 1,771 ms across the nine measurements that reached the audit), inside the 2,000 ms limit. This is the tightest margin in the evaluation. A submission costs two round trips, because the server answers a form with a redirect (Post/Redirect/Get), and the last answer of an assessment costs three. The first visit transfers 8.2 kB in 3 requests; later screens average 1.67 requests, because static files carry a content fingerprint and are cached.

### 2.6 Tests, static analysis, and recovery (NFR-05, NFR-09, NFR-12)

| Check | Result |
|---|---|
| Automated tests (Python 3.11 and 3.13) | 233 passed, 6 subtests passed |
| Statement coverage of `src/` | 100% (1,280 statements, 0 missed) |
| Static security analysis (Bandit) | No issues identified. |
| Dependency audit (pip-audit) | No known vulnerabilities found |
| Packages in the image | The image holds the 11 pinned packages and nothing else. |
| TLS versions accepted by the proxy | 1.2 and 1.3; 1.1 is refused |
| Restore rehearsal on the running stack | Services stopped after 1 s. Backup restored after 1 s. All services healthy again after 12 s. The site failed 5 health request(s); it was unavailable for at most 1.4 seconds. |

The restore rehearsal stops the web service and the jobs service, restores the newest encrypted backup, and starts both again, while a probe requests the health endpoint about five times a second. The time for which the site did not answer is also the time a participant would wait during an upgrade, because an upgrade recreates the same two services.

### 2.7 Variation between measurements

The pipeline repeats the measurements on every push. On the evening of 8 October 2026 (21:53 to 23:10 UTC) it measured the same application code ten times: runs 24 to 32, of which run 32 was made twice. The commits of these runs differ in documentation, in the version number, and in the CI workflow, not in the application. Each measurement ran on a freshly started hosted runner of the same type.

| Measurement | Commit | Requests per second at 25 | Answers at 25: 95th percentile (ms) | Pages at 25: 95th percentile (ms) | Budget of NFR-01 | Answers at 25 within 500 ms | Lowest level with an answer over 500 ms | Answers at 200 within 500 ms | Page loads over 2,000 ms, all levels | Failed requests | Reported values correct |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 24 | `2688878` | 563 | 79.4 | 91.6 | met | 800 of 800 | 10 participants | 6,360 of 6,400 (99.4%) | 0 | 0 | 14,566 of 14,566 |
| 25 | `99888be` | 500 | 91.0 | 100.2 | met | 800 of 800 | 50 participants | 6,173 of 6,400 (96.5%) | 0 | 0 | 14,566 of 14,566 |
| 26 (reported) | `04f8f1b` | 540 | 99.3 | 96.7 | met | 800 of 800 | 50 participants | 6,324 of 6,400 (98.8%) | 0 | 0 | 14,566 of 14,566 |
| 27 | `7250fee` | 539 | 107.5 | 104.3 | met | 799 of 800 | 25 participants | 6,214 of 6,400 (97.1%) | 0 | 0 | 14,566 of 14,566 |
| 28 | `4beb207` | 515 | 92.4 | 99.7 | met | 800 of 800 | 50 participants | 6,279 of 6,400 (98.1%) | 0 | 0 | 14,566 of 14,566 |
| 29 | `652c17f` | 532 | 93.1 | 95.6 | met | 800 of 800 | 10 participants | 6,115 of 6,400 (95.5%) | 36 | 0 | 14,566 of 14,566 |
| 30 (slow runner) | `9d4d17b` | 198 | 413.3 | 372.1 | met | 768 of 800 | 5 participants | 1,511 of 6,400 (23.6%) | 657 | 0 | 14,566 of 14,566 |
| 31 | `b60399e` | 570 | 82.7 | 80.9 | met | 800 of 800 | 10 participants | 6,307 of 6,400 (98.5%) | 0 | 0 | 14,566 of 14,566 |
| 32, first attempt (slow runner) | `4be5b6e` | 103 | 972.9 | 1,055.6 | **failed** | 693 of 800 | 5 participants | 3,543 of 6,400 (55.4%) | 299 | 0 | 14,566 of 14,566 |
| 32, second attempt | `4be5b6e` | 503 | 95.3 | 102.2 | met | 800 of 800 | 10 participants | 6,138 of 6,400 (95.9%) | 0 | 0 | 14,566 of 14,566 |

**Eight measurements agree, and two do not.** In eight measurements the stack served 500 to 570 requests per second at 25 participants, and the 95th percentile was 79.4 to 107.5 ms for answers and 80.9 to 104.3 ms for pages: the limit was more than 4.6 times the measured value for answers and 19 times for pages. In the other two measurements the whole test ran at less than half that speed.

- **Run 30** served 198 requests per second at 25 participants and still met the budget, with 413.3 ms for answers against a limit of 500 ms.
- **Run 32, first attempt,** served 103 requests per second at 25 participants and missed the budget for answers, with 972.9 ms, so the job failed, as it is meant to. The job was then repeated on the same commit, on another runner, and passed with 95.3 ms.

Runs 30, 31, and 32 started within a minute of one another on three runners and tested identical files. One was as fast as the earlier runs, one was slow, and one failed.

- **What this shows.** The difference lies in the machine and not in the code: identical code met the budget with a wide margin, met it narrowly, or missed it, depending on the runner it was given. Which resource was short on the slow runners was not determined. Every answer is written to disk before it is confirmed, so the speed of the disk is one candidate; processor time taken by other virtual machines on the same host is another.
- **What it means for NFR-01.** The requirement is met on a machine that performs like the faster runners. It is not met on every machine, so the result cannot be carried over to the pilot host: that host must be measured with the same test before the pilot (section 6). A timing budget that is checked on shared runners will also fail now and then for a reason outside the code.
- **Single slow answers.** Even on the faster runners, single answers exceeded the limit. Over these eight measurements, 6,399 of 6,400 answers at 25 participants were processed within 500 ms; the exception took 858 ms, in run 27. All 9,000 page loads at that level were delivered within 2,000 ms. Single answers above 500 ms appeared from 10 participants upward in some measurements and not before 50 in others. Over all levels, 115,017 of 116,480 answers (98.7%) and 163,764 of 163,800 page loads were inside their limits. NFR-01 does not name a percentile; read as a limit on every single request, it was missed by one answer at the required load even on the faster runners.
- **Cause of the single slow answers.** It has not been found. On the faster runners they almost disappear with write-ahead logging (section 2.2), which suggests that they are waits for the database's write lock, but that has not been verified. The load generator also shares the processor with the stack (section 5).
- **What did not vary.** In all ten measurements, the slow ones included, none of the 382,200 requests failed and 145,660 of 145,660 reported values matched the independent calculation. The accessibility audit gave the same result in each of the nine measurements that reached it. During the restore rehearsals the site did not answer for at most 2.3 seconds.

## 3. Qualitative results

### 3.1 Accessibility (NFR-03)

The audit walks through all 21 screens of the participant journey and the administrator area in a real browser.

- **axe-core 4.10.2**, with the WCAG 2.1 level A and AA rules and the best-practice rules: no rule was violated. One rule needed a manual review: the engine cannot work out the background behind text inside the SVG charts. The labels are `#5b6b80` on white, a contrast of 5.44:1, which meets the 4.5:1 that success criterion 1.4.3 requires.
- **Scripted checks** (1,915 in total, none failed):

| Check | WCAG criterion | Checked | Failed |
|---|---|---|---|
| Text contrast at least 4.5:1 (3:1 for large text) | 1.4.3 | 828 | 0 |
| Text-field boundary contrast at least 3:1 | 1.4.11 | 2 | 0 |
| Visible focus indicator on every tab stop | 2.4.7 | 131 | 0 |
| Focus indicator contrast at least 3:1 | 1.4.11 | 131 | 0 |
| Every control reachable with the Tab key | 2.1.1 | 131 | 0 |
| Accessible name on every control | 4.1.2 | 174 | 0 |
| Images and graphics named or hidden | 1.1.1 | 36 | 0 |
| Radio buttons grouped under a legend | 1.3.1 | 56 | 0 |
| Language, title, headings, landmarks, skip link | several | 126 | 0 |
| No horizontal scrolling at 320 to 1920 px | 1.4.10 | 105 | 0 |
| No clipped text with wider text spacing | 1.4.12 | 21 | 0 |
| Touch targets 24 by 24 px or spaced apart (WCAG 2.2) | 2.5.8 | 174 | 0 |

- **Keyboard only** (2.1.1, 2.1.2): the audit withdrew once and then completed a whole journey with 432 key presses and no pointer.

Automated checks find only part of the accessibility problems that exist. Criteria that need human judgement, such as whether a heading describes its section or whether the reading order makes sense, have not been reviewed systematically, and the application has not been tested with a screen reader or by people who rely on assistive technology.

### 3.2 Browsers and screen widths (NFR-04)

| Engine | Version | Screens | Journey | Layout failures at 360 and 1,920 px |
|---|---|---|---|---|
| Chromium | 141.0.7390.37 | 21 | completed | 0 (also checked at 320, 768, and 1,280 px) |
| Firefox | 142.0.1 | 21 | completed | 0 |
| WebKit (the engine of Safari) | 26.0 | 21 | completed | 0 |

The same journey, including withdrawal, the administrator area, and Finish, completes in all three engines, and no screen scrolls sideways at either end of the supported range.

### 3.3 Usability inspection (NFR-02)

**Method.** A heuristic evaluation compares an interface with recognised usability principles. Every screen was captured at 360 px and at 1,280 px and compared with Nielsen's ten heuristics; the states that a normal journey does not reach (validation errors, a refused sign-in, an expired security token, an expired session, and the dashboard below its reporting threshold) were opened as well. Each finding is rated on Nielsen's scale: 0 not a problem, 1 cosmetic, 2 minor, 3 major, 4 catastrophe.

**Earlier rounds.** Two rounds during development found ten usability problems, all corrected before this release; the commit history records each with its fix. The most important were that a look-alike address broke at its hyphen on a phone, so that phone users saw a different item from laptop users, and that a single click recorded an assessment answer for good. The scripted audit found four accessibility failures in the same period (focus-ring and field-border contrast, sideways scrolling at 320 px, and small targets), also corrected.

**Final round, on this release.**

| Heuristic | What the interface does | Open findings |
|---|---|---|
| 1. Visibility of system status | Six numbered steps with a status for each; an item counter and a progress bar in every assessment; the score after each phase | U-1 |
| 2. Match between the system and the real world | Scenarios look like an email client and a browser window; plain language; no security jargon without an explanation | none |
| 3. User control and freedom | Withdraw on every page; a choice can be changed until it is submitted; a participant can leave and resume | none |
| 4. Consistency and standards | One primary action per screen in one style; assessment and practice share one layout | none |
| 5. Error prevention | An answer is chosen and then submitted; withdrawal asks for confirmation; a second click cannot submit a form twice | none |
| 6. Recognition rather than recall | Feedback shows the scenario again with the real link destination revealed; the results page names the cues to practise | none |
| 7. Flexibility and efficiency of use | The whole journey works with the keyboard alone | U-3 |
| 8. Aesthetic and minimalist design | One scenario per screen; the scenario is the only raised surface | U-5 |
| 9. Help users recognize, diagnose, and recover from errors | Validation messages say what to do next; a rejected survey keeps the answers already given | U-4 |
| 10. Help and documentation | The consent page explains every step; a hint sits beside each task | U-2 |

| # | Finding | Heuristic | Severity |
|---|---|---|---|
| U-1 | When a session expires after two idle hours, the next request returns the participant to the consent form without saying why | 1 | 2 (minor) |
| U-2 | The researcher's contact line appears only on the consent page; a participant with a question during the session finds no contact or help link | 10 | 2 (minor) |
| U-3 | At 360 px the usability survey stacks its five options, so the page is 3,941 px long, about five screens | 7 | 1 (cosmetic) |
| U-4 | The "Page not found" text is the framework's default wording and shows an error code; it is more technical than the rest of the interface | 9 | 1 (cosmetic) |
| U-5 | In a feedback list, an opening parenthesis can be left at the end of a line, separated from the domain that follows it | 8 | 1 (cosmetic) |

No finding is rated major or catastrophic. The five open findings are planned for release 0.7.0.

**What this inspection is not.** It was carried out by one evaluator, who is also the developer. Nielsen recommends three to five independent evaluators and describes severity ratings from a single evaluator as unreliable. The inspection therefore shows that known usability principles were applied and where they are still violated; it does not show how real participants experience the system. The System Usability Scale survey that is built into the application will measure that in the pilot (objective O5: a mean score of at least 68).

## 4. Summary against the requirements

| Requirement | Evidence | Result |
|---|---|---|
| NFR-01 Performance: answers within 0.5 s and pages within 2 s for 25 concurrent users | 95th percentile at 25 participants in the reported run: 99.3 ms for answers, 96.7 ms for pages. Eight of ten measurements: 79.4 to 107.5 ms and 80.9 to 104.3 ms. Two measurements on slow runners: 413.3 ms for answers (met) and 972.9 ms for answers (failed) (section 2.7). Page load on a slow connection: 1,759 ms at the 95th percentile | Met on runners of normal speed; missed once on a slow runner. To be measured on the pilot host |
| NFR-02 Usability: completion within 30 minutes; SUS of at least 68 | Needs participants. Inspection: five open findings, none above minor | Not yet measured |
| NFR-03 Accessibility: core WCAG 2.1 AA checks | 0 axe-core violations; 1,915 scripted checks, none failed; keyboard-only journey completed | Met for the automated checks |
| NFR-04 Portability: current browsers at 360 to 1,920 px | Journey completed in Chromium, Firefox, and WebKit; no layout failure | Met |
| NFR-05 Reliability: no loss of submitted answers | 14,566 of 14,566 reported values correct under load; restore rehearsed on the running stack | Met in these measurements; **not met by release 0.6.0** (defect D-7, corrected in 0.7.0; see the correction at the top) |
| NFR-06 Scalability: 50 pilot users | 200 concurrent journeys completed, 0 failed requests | Met |
| NFR-08 to NFR-12 Security, privacy, and compliance | `docs/security-checklist.md`: 52 of 54 applicable ASVS 5.0 Level 1 requirements met, none open above low | Met |

## 5. Limits of this evaluation

- **No participants.** The measurements show that the system works, responds in time, and computes its results correctly. They do not show that it teaches anything.
- **One machine.** The load generator ran on the same runner as the stack, so both shared four processor cores, and no real network lay between them. A separate client would leave the server more processor time and add network delay. A host with fewer cores has not been measured.
- **Shared hardware.** GitHub-hosted runners are virtual machines on shared hosts, so timings vary from run to run. Two of the ten measurements ran at less than half the usual speed, and one of them failed the budget (section 2.7). The response times reported here describe the faster runners, not the pilot host.
- **No think time.** Simulated participants answer at once. The levels are therefore harder than the same number of people, and the results do not predict behaviour over a long session.
- **Short runs.** A level lasts seconds, not hours. The test does not show memory growth or behaviour as the database grows over weeks.
- **One evaluator.** See section 3.3.
- **Automated accessibility checks** cover only part of WCAG. See section 3.1.

## 6. Reproducing the measurements

Start the stack as `docs/deployment.md` describes, with an empty database, and create an administrator. Both tools create scripted records, so never run them against the database of a live study.

```bash
python evaluation/loadtest.py --base-url https://<host> --levels 1,5,10,25,50,100,200 --min-journeys 25 \
    --admin-user evaluator --admin-password "..."

pip install -r evaluation/requirements.txt && python -m playwright install chromium firefox webkit
npm install --no-save axe-core@4.10.2
python evaluation/browser_audit.py --base-url https://<host> --axe node_modules/axe-core/axe.min.js \
    --also firefox,webkit --admin-user evaluator --admin-password "..."
```

Both tools exit with an error when a budget is exceeded or a check fails, and `--json` writes the complete result to a file. The usability inspection is repeated by adding `--screenshots <folder>` to the audit, which saves every screen at 360 px and 1,280 px.
