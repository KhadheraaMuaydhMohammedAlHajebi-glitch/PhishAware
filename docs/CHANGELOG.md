# Changelog

All notable changes to PhishAware are recorded here (Keep a Changelog format).

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

### Planned
- 0.5.0 (Unit 5): post-assessment (FR-07), results page (FR-08), SUS survey (FR-09).
- 0.6.0 (Unit 6): administrator reporting (FR-11), encryption at rest, and pilot deployment.
