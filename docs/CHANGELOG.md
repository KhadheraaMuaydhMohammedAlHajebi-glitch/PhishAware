# Changelog

All notable changes to PhishAware are recorded here (Keep a Changelog format).

## [Unreleased]

## [0.6.0] - 2026-10-09 (Unit 6: integration, evaluation, and deployment)

### Added
- M7 Admin Reporting (FR-11): administrator sign-in with scrypt-hashed passwords, a rate limit keyed on the user name instead of an IP address, a 15-minute idle limit and an 8-hour session limit; an aggregate dashboard that shows statistics only from five completed participants; and a de-identified CSV export without identifiers or timestamps.
- M8 data protection: a retention job that deletes records 90 days after consent, backups encrypted with AES-256-GCM that expire after 7 days, a restore command that checks authenticity, integrity, and schema first, and a `run-jobs` service with its own health check (NFR-05, NFR-11, NFR-12).
- M1: consent form 1.1, which states the retention period, the backup period, and the session limit, and a Finish step that ends the session on the server (FR-01, FR-10).
- M3 and M4: opening the lessons is recorded as a step and unlocks the practice scenarios; an answer is chosen first and then submitted with a separate button (FR-04, FR-05).
- Deployment: a production image (`Dockerfile`), a three-service stack with an HTTPS reverse proxy (`docker-compose.yml`, `deploy/Caddyfile`), server settings (`gunicorn.conf.py`), a settings template (`.env.example`), exact versions of all eleven packages (`constraints.txt`), start-up checks that refuse an unsafe configuration, and a health endpoint (`/healthz`) (NFR-08, NFR-12).
- Evaluation tools: `evaluation/loadtest.py` (latency, throughput, and the accuracy of every reported score against an independent oracle) and `evaluation/browser_audit.py` (accessibility, layout from 320 to 1920 px, keyboard-only use, page-load time, and the complete journey repeated in Firefox and WebKit) (NFR-01, NFR-03, NFR-04).
- CI: a quality gate on Python 3.11 and 3.13, a container job that builds the image, smoke-tests the stack over HTTPS, and rehearses the restore procedure, and an evaluation job that measures the running stack on every push. Every job has a time limit, and a stalled browser download is repeated.
- Documents: `docs/deployment.md`, `docs/evaluation.md`, and `docs/security-checklist.md` (a review against OWASP ASVS 5.0).
- 155 new tests (233 in total); statement coverage of `src/` remains 100% (1,280 statements).

### Changed
- The session cookie is signed with HMAC-SHA-256 and, over HTTPS, carries the `__Host-` prefix; the content security policy adds `object-src 'none'` and `base-uri 'none'`; responses that end a session send `Clear-Site-Data`; HTTPS deployments send `Strict-Transport-Security`.
- SQLite's journal mode is chosen from the library release (`PHISHAWARE_SQLITE_JOURNAL=AUTO`): write-ahead logging only where the WAL-reset bug is fixed, the rollback journal otherwise.
- Static files carry a content fingerprint and may be cached for a year (NFR-01).
- Addresses and domains in scenarios, lessons, and feedback are rendered as units that cannot break inside a label (NFR-04).
- The results page states that both forms follow the same plan; it no longer claims equal difficulty, which has not been measured.
- README: the release steps now describe how each version tag is created, by publishing a GitHub Release from the release branch.

### Fixed
- Restoring an empty or non-SQLite backup ended with an unhandled `MemoryError`; the file is now rejected with a clear message.
- A copy of a session cookie stayed valid after Finish or sign-out (OWASP ASVS 7.4.1); the end of a session is now recorded on the server.
- Backups were kept without an age limit, so a deleted record could survive in them.
- Accessibility audit: the keyboard focus ring on the top bar (2.41:1) and text-field borders (2.93:1) were below the 3:1 contrast that WCAG 1.4.11 requires, the dashboard scrolled sideways at 320 px, and top-bar links were smaller than the 24 px minimum target.
- Pages took more than two seconds on an emulated slow mobile connection, because static files were revalidated on every view.

### Security
- OWASP ASVS 5.0, Level 1: of 70 requirements, 16 do not apply, 52 are met, one is an accepted deviation, and one is open (6.2.4, a check against common passwords, rated low). No critical or high finding is open (NFR-12).

### Planned
- 0.7.0 (Unit 7): system and acceptance testing, the open findings of the usability inspection, the common-password check, and the pilot with participants once it is approved.

## [0.5.1] - 2026-10-07 (Unit 5: testing completion)

### Added
- Statement-coverage measurement with coverage.py (`.coveragerc`, development requirement, and a 90% threshold in the CI workflow).
- 12 new tests (78 in total) for the dashboard, lessons, navigation guards, data tier, analytics edge cases, and chart boundaries.

### Changed
- Statement coverage of `src/` rose from 94% to 100%; `progress.py`, which no test had exercised through the dashboard, rose from 44% to 100%.

## [0.5.0] - 2026-10-07 (Unit 5: core logic and unit testing)

### Added
- M2: counterbalanced post-assessment on the participant's second form, unlocked after the practice phase (FR-07).
- M5: cue comparison, focus areas, and cohort statistics (mean gain, standard deviation, paired t, and Cohen's d_z); a personal results page with a CSP-safe SVG chart; and the `analytics` command for the three research questions (FR-08).
- M6: ten-item System Usability Scale survey with allowlist validation and idempotent storage (FR-09).
- `scripts/demo_core_logic.py`, which runs the core algorithms on inputs that can be checked by hand.
- 27 new tests (66 in total): white-box tests for the analytics functions and black-box tests for the post-assessment, results page, survey, and command-line tools.

### Changed
- The assessment engine is phase-generic: the pre- and post-assessment share one code path driven by small configuration tables.

### Fixed
- The command-line tools failed under Flask's test runner with "Working outside of application context"; every command now uses `with_appcontext`.
- `sus_score` accepted boolean ratings because `bool` is a subclass of `int`; they are now rejected.

### Planned
- 0.6.0 (Unit 6): administrator reporting (FR-11), encryption at rest, and pilot deployment.

## [0.4.0] - 2026-09-30 (Unit 4: initial implementation)

### Added
- M1 Consent & Session: versioned consent (v1.0), adult confirmation, random UUID4 IDs, and withdrawal with cascade deletion (FR-01, FR-02, FR-10).
- M2 Assessment Engine: counterbalanced 12-item pre-assessment with idempotent answers (FR-03, NFR-05).
- M3 Learning Content: six cue lessons stored as data (FR-04).
- M4 Scenario Practice & Feedback: six practice scenarios with immediate, cue-level feedback (FR-05, FR-06).
- M5 scoring functions: score, learning gain, cue error rates, SUS score, counterbalancing, and form validation.
- M8 security layer: CSRF tokens, allowlist validation, parameterized queries, CSP and security headers, hardened session cookies, and logs without IP addresses.
- Fictional 30-scenario bank (Forms A and B plus a practice pool) using reserved domains only.
- Automated unit tests and a GitHub Actions CI workflow.

### Notes
- The prototype was completed for the Unit 4 submission; it was imported into Git and tagged `v0.4.0` on 2026-10-07.

### Planned
- 0.5.0 (Unit 5): post-assessment (FR-07), results page (FR-08), SUS survey (FR-09).
- 0.6.0 (Unit 6): administrator reporting (FR-11), encryption at rest, and pilot deployment.
