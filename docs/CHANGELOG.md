# Changelog

All notable changes to PhishAware are recorded here (Keep a Changelog format).

## [Unreleased]

### Changed
- README: the release steps now describe how each version tag is created, by publishing a GitHub Release from the release branch.

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
