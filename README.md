# PhishAware

An interactive web application that helps university students recognize phishing emails and websites through fictional scenarios and immediate, cue-by-cue feedback.
MSIT 5910 Capstone Project, University of the People.

**Release 0.5.1 (Unit 5 core logic and unit testing)**

## What works in this release

| Module | What it does | Requirements |
|---|---|---|
| M1 Consent & Session | Versioned consent, adult confirmation, random UUID4 session, withdrawal that deletes everything | FR-01, FR-02, FR-10 |
| M2 Assessment Engine | Counterbalanced 12-item pre- and post-assessment on parallel Forms A and B | FR-03, FR-07 |
| M3 Learning Content | Six one-minute lessons, one per phishing cue | FR-04 |
| M4 Practice & Feedback | Six practice scenarios with immediate, cue-level explanations | FR-05, FR-06 |
| M5 Scoring, Results & Analytics | Personal results page with a per-cue chart and focus areas; cohort statistics (mean gain, Cohen's d_z, paired t, cue error rates) | FR-08 |
| M6 Usability Survey | Ten-item System Usability Scale with allowlist validation | FR-09 |
| M8 Security | CSRF tokens, allowlist validation, parameterized SQL, CSP headers, hardened cookies, IP-free logs | NFR-08 to NFR-12 |

Planned: administrator reporting (FR-11), encryption at rest, and pilot deployment in 0.6.0.

## Quick start

Requires Python 3.10 or newer. SQLite ships with Python, so no database server is needed.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
source .venv/bin/activate        # macOS / Linux
pip install -r requirements.txt
flask --app src.app init-db      # creates the schema and loads 30 scenarios
python run.py                    # open http://127.0.0.1:5000
```

## Useful commands

```bash
python -m unittest discover -s tests -t .   # run all tests (or: pip install pytest && pytest)
coverage run -m unittest discover -s tests -t . && coverage report   # statement coverage of src/
flask --app src.app analytics               # cohort statistics for the three research questions
python scripts/demo_core_logic.py           # core algorithms on inputs you can check by hand
python scripts/inspect_db.py                # privacy-preserving summary of stored data
flask --app src.app reset-db                # development only: delete all participant data
```

## Project structure

```
PhishAware/
  run.py                  development server entry point
  requirements.txt        pinned runtime dependency (Flask 3.1.3)
  src/
    app.py                application factory (wires the three tiers)
    config.py             settings read from environment variables
    db.py, schema.sql     data tier: SQLite connection, schema, seeding
    repository.py         all SQL, always parameterized
    modules/              consent (M1), assessment (M2), learning (M3), practice (M4),
                          scoring, results, analytics (M5), survey (M6),
                          security (M8), progress
    templates/            Jinja2 pages (auto-escaped)
    static/               CSS, JavaScript, favicon (no inline code, CSP-friendly)
  data/                   scenarios.json and lessons.json (content as data)
  tests/                  unit, black-box, data-integrity, and security tests
  scripts/                inspect_db.py (database summary), demo_core_logic.py (algorithms)
  docs/CHANGELOG.md       release history
  docs/releases/          release notes for each tag
  design/                 Unit 3 design artifacts
  .github/workflows/      CI pipeline (flake8, pytest with coverage, Bandit, pip-audit)
```

## Git workflow

The repository follows a Git Flow-style model:

- `main` holds only released versions. Every release is an annotated tag that follows Semantic Versioning, for example `v0.4.0`.
- `develop` is the integration branch for finished work that has passed the tests.
- `feature/<module>-<topic>` branches are short-lived and merge into `develop` with `--no-ff`, so each feature keeps a visible merge commit.
- `fix/<topic>` branches correct defects, and `release/<version>` branches update the version, changelog, and release notes before a release.
- Commits follow Conventional Commits with requirement IDs, for example `feat(M4): add immediate feedback page [FR-06]`.

Cutting a release:

```bash
git checkout -b release/0.4.0 develop        # update the version, changelog, and docs/releases
git checkout main && git merge --no-ff release/0.4.0
git tag -a v0.4.0 -m "v0.4.0: Unit 4 milestone - initial implementation"
git checkout develop && git merge --no-ff release/0.4.0
git push origin main develop --tags
```

Release notes for each tag are kept in `docs/releases/` and published as GitHub Releases.

## Security, privacy, and ethics

- No data is stored before consent; the only identifier is a random UUID4.
- No names, email addresses, student numbers, passwords, IP addresses, or demographics are collected.
- Withdrawal deletes every linked record through `ON DELETE CASCADE`.
- All scenarios are fictional and use reserved `.example` and `.test` domains; links and forms are inert.
- No real phishing emails are ever sent, and no real university systems are contacted.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `PHISHAWARE_SECRET_KEY` | Session signing key (set in any shared environment) | random per start |
| `PHISHAWARE_DB` | SQLite file path | `instance/phishaware.db` |
| `PHISHAWARE_COOKIE_SECURE` | `1` to send cookies only over HTTPS | `0` |
| `PORT` | Development server port | `5000` |
| `FLASK_DEBUG` | `1` enables the debugger (never in pilots) | off |
