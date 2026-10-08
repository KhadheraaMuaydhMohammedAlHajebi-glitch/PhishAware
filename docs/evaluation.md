# Evaluation of PhishAware 0.6.0

This document reports how release 0.6.0 was measured and what the measurements show. It uses two kinds of evidence: quantitative metrics, which a tool counts or times, and qualitative assessment, which rests on an evaluator's judgement against published criteria.

**No participant has used the system yet.** Every figure below comes from scripted sessions or from inspection. The figures describe the software. They say nothing about whether students learn from it; the pilot answers that question (RQ1 to RQ3), and it has not started.

## 1. Method

| | |
|---|---|
| System under test | The stack that `docs/deployment.md` describes: Caddy 2 for HTTPS, Gunicorn with 2 worker processes x 4 threads, and SQLite in journal mode `delete`, built from commit `04f8f1b` (release candidate 0.6.0) |
| Software in the image | Python 3.13.16, Flask 3.1.3, Werkzeug 3.1.9, Gunicorn 26.2.0, SQLite 3.46.1 |
| Test machine | A GitHub-hosted runner: 4 CPUs, 15.6 GB of memory, Ubuntu 24.04.5 LTS, kernel 6.17.0-1022-azure; Docker 28.0.4, Compose 2.38.2 |
| Run reported | CI run 26 of 8 October 2026 (UTC). The tables report this run. Where a range is given, it covers runs 24 to 29, the six runs that measured this application code, each on a runner of the same type; section 2.7 compares them |
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

At the required load of 25 participants, 95% of answers were processed within 99.3 ms and 95% of pages were delivered within 96.7 ms. In this run all 800 answers and all 1,125 page loads at that level stayed inside their limits. Across runs 24 to 29 the 95th percentile at 25 participants ranged from 79.4 to 107.5 ms for answers and from 91.6 to 104.3 ms for pages.

A percentile does not show how often a single request is slow, so the table also counts the requests inside each limit. In this run the first answer slower than 500 ms appeared at 50 participants. At 200 participants, eight times the required load, 6,324 of 6,400 answers (98.8%) were inside the limit and the 95th percentile was 449.7 ms (430.0 to 489.6 ms across the runs). No page load exceeded 2,000 ms at any level. The slowest requests differ from run to run more than the percentiles do; section 2.7 reports them for every run.

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

**Journal mode.** The image ships SQLite 3.46.1, which has the WAL-reset bug that SQLite's documentation describes, so the default setting `AUTO` selects the rollback journal. The last column repeats the test with write-ahead logging forced on: 748 to 775 requests per second, about 36% more, and a 95th percentile of 47.0 ms for answers at 25 participants instead of 99.3 ms. Across the six runs the gain ranged from 32% to 92%. Write-ahead logging also removes most of the slow answers: 7 of 87,360 answers took longer than 500 ms over all levels of all runs, all at 200 participants, against 1,097 of 87,360 with the rollback journal. The cautious default costs throughput and slow answers under heavy load, and it still meets the budgets of NFR-01 with a wide margin, so it stays. `AUTO` will select write-ahead logging by itself once the image contains a fixed SQLite.

### 2.3 Accuracy of reported results (FR-08, FR-09, FR-11)

The load test scripts every answer from a fixed seed, so it knows what each score must be. It computes the expected values with its own code, written independently of the application, and compares them with what the system shows. **14,566 of 14,566 reported values matched**: 455 journeys x 15 results-page checks; 455 export rows x 17 values; 5 dashboard counts; 1 row count. The check covers the personal results page (both scores, the gain, and the percentage correct for each cue before and after), the administrator's dashboard, and every value in the de-identified export, including each System Usability Scale score.

The result holds under load: the values were produced while up to 200 journeys ran at once, so no answer was lost, duplicated, or attributed to another participant.

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

Without throttling, 95% of navigations finished within 27 ms. On the emulated slow connection, opening a screen took 612 ms at the median, and submitting a form and receiving the next screen took 1,198 ms at the median and 1,759 ms at the 95th percentile (1,741 to 1,769 ms across the runs), inside the 2,000 ms limit. This is the tightest margin in the evaluation. A submission costs two round trips, because the server answers a form with a redirect (Post/Redirect/Get), and the last answer of an assessment costs three. The first visit transfers 8.2 kB in 3 requests; later screens average 1.67 requests, because static files carry a content fingerprint and are cached.

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

### 2.7 Variation between runs

The pipeline repeats the measurements on every push, so the same application code was measured six times on 8 October 2026, each time on a freshly started hosted runner. Every run passed every budget and every check. The runs differ in the slowest requests.

| Run | Commit | Answers at 25: 95th percentile (ms) | Pages at 25: 95th percentile (ms) | Answers at 25 within 500 ms | Lowest level with an answer over 500 ms | Answers at 200 within 500 ms | Page loads over 2,000 ms, all levels | Requests per second, 5 to 200 participants | Slow 4G, submit to next screen: 95th percentile (ms) | Longest outage in the restore rehearsal (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| 24 | `2688878` | 79.4 | 91.6 | 800 of 800 | 10 participants | 6,360 of 6,400 (99.4%) | 0 | 552 to 577 | 1,760 | 1.3 |
| 25 | `99888be` | 91.0 | 100.2 | 800 of 800 | 50 participants | 6,173 of 6,400 (96.5%) | 0 | 499 to 519 | 1,769 | 1.5 |
| 26 (reported) | `04f8f1b` | 99.3 | 96.7 | 800 of 800 | 50 participants | 6,324 of 6,400 (98.8%) | 0 | 540 to 594 | 1,759 | 1.4 |
| 27 | `7250fee` | 107.5 | 104.3 | 799 of 800 | 25 participants | 6,214 of 6,400 (97.1%) | 0 | 539 to 561 | 1,741 | 1.8 |
| 28 | `4beb207` | 92.4 | 99.7 | 800 of 800 | 50 participants | 6,279 of 6,400 (98.1%) | 0 | 511 to 525 | 1,760 | 2.2 |
| 29 | `652c17f` | 93.1 | 95.6 | 800 of 800 | 10 participants | 6,115 of 6,400 (95.5%) | 36 | 330 to 600 | 1,768 | 2.0 |

- **Budgets.** The gate compares the 95th percentile at 25 participants with the limits of NFR-01. Every run met it: the limit was more than 4.6 times the measured value for answers and 19 times for pages.
- **Single slow answers.** Over the six runs, 4,799 of 4,800 answers at 25 participants were processed within 500 ms and all 6,750 page loads at that level within 2,000 ms. The exception was one answer in run 27, which took 858 ms. Single answers above 500 ms appeared from 10 participants upward in some runs and not before 50 in others. Over all levels, 86,263 of 87,360 answers (98.7%) and 122,814 of 122,850 page loads were inside their limits; the 36 slow page loads all occurred in run 29 at 200 participants.
- **Reading of the requirement.** NFR-01 does not name a percentile. Measured at the 95th percentile, it is met in every run. Read as a limit on every single request, it was missed by one answer at the required load.
- **Cause.** The slow answers have not been traced to a cause. They almost disappear with write-ahead logging (section 2.2), which suggests that they are waits for the database's write lock, but that has not been verified. The load generator also shares the processor with the stack (section 5).
- **What did not vary.** None of the 229,320 requests in the six runs failed, and 87,396 of 87,396 reported values matched the independent calculation. The accessibility audit gave the same result in every run. The time for which the site did not answer during the restore rehearsal was at most 2.2 seconds.

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
| NFR-01 Performance: answers within 0.5 s and pages within 2 s for 25 concurrent users | 95th percentile at 25 participants: 99.3 ms for answers, 96.7 ms for pages (79.4 to 107.5 ms and 91.6 to 104.3 ms across six runs); 4,799 of 4,800 answers at that load within the limit; page load on a slow connection 1,759 ms at the 95th percentile | Met at the 95th percentile |
| NFR-02 Usability: completion within 30 minutes; SUS of at least 68 | Needs participants. Inspection: five open findings, none above minor | Not yet measured |
| NFR-03 Accessibility: core WCAG 2.1 AA checks | 0 axe-core violations; 1,915 scripted checks, none failed; keyboard-only journey completed | Met for the automated checks |
| NFR-04 Portability: current browsers at 360 to 1,920 px | Journey completed in Chromium, Firefox, and WebKit; no layout failure | Met |
| NFR-05 Reliability: no loss of submitted answers | 14,566 of 14,566 reported values correct under load; restore rehearsed on the running stack | Met |
| NFR-06 Scalability: 50 pilot users | 200 concurrent journeys completed, 0 failed requests | Met |
| NFR-08 to NFR-12 Security, privacy, and compliance | `docs/security-checklist.md`: 52 of 54 applicable ASVS 5.0 Level 1 requirements met, none open above low | Met |

## 5. Limits of this evaluation

- **No participants.** The measurements show that the system works, responds in time, and computes its results correctly. They do not show that it teaches anything.
- **One machine.** The load generator ran on the same runner as the stack, so both shared four processor cores, and no real network lay between them. A separate client would leave the server more processor time and add network delay. A host with fewer cores has not been measured.
- **Shared hardware.** GitHub-hosted runners are virtual machines on shared hosts, so timings vary from run to run; section 2.7 shows by how much.
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
