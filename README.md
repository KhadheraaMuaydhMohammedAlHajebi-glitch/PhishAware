# PhishAware

An interactive web application that helps university students recognize phishing emails and websites through fictional scenarios and immediate, cue-by-cue feedback.
MSIT 5910 Capstone Project, University of the People.

**Release 0.6.0 (Unit 6: integration, evaluation, and deployment)**

## What works in this release

| Module | What it does | Requirements |
|---|---|---|
| M1 Consent & Session | Versioned consent form that states what is stored and for how long, adult confirmation, random UUID4 session, withdrawal that deletes everything, and a Finish step that ends the session | FR-01, FR-02, FR-10 |
| M2 Assessment Engine | Counterbalanced 12-item pre- and post-assessment on parallel Forms A and B; an answer is chosen and then confirmed | FR-03, FR-07 |
| M3 Learning Content | Six one-minute lessons, one per phishing cue; opening them is recorded and unlocks the practice | FR-04 |
| M4 Practice & Feedback | Six practice scenarios with immediate, cue-level explanations | FR-05, FR-06 |
| M5 Scoring, Results & Analytics | Personal results page with a per-cue chart and focus areas; cohort statistics (mean gain, Cohen's d_z, paired t, cue error rates) | FR-08 |
| M6 Usability Survey | Ten-item System Usability Scale with allowlist validation | FR-09 |
| M7 Admin Reporting | Password-protected dashboard with aggregate results (from five completed participants) and a de-identified CSV export | FR-11 |
| M8 Security & Data Protection | Anti-forgery tokens, allowlist validation, parameterized SQL, strict content security policy, hardened cookies, logs without IP addresses, a retention job, and encrypted backups | NFR-05, NFR-08 to NFR-12 |

All eleven functional requirements are implemented. The pilot with participants is the next step and needs approval first; no participant data has been collected yet.

## Documents

| Document | Content |
|---|---|
| [`docs/deployment.md`](docs/deployment.md) | Prerequisites, settings, install and start commands, operation, upgrade, rollback |
| [`docs/evaluation.md`](docs/evaluation.md) | How the system was measured, and the results: latency, throughput, accuracy, memory, accessibility, and the usability inspection |
| [`docs/security-checklist.md`](docs/security-checklist.md) | The review against OWASP ASVS 5.0, with the test behind each entry |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md), [`docs/releases/`](docs/releases) | Release history and notes |

## Quick start (development)

Requires Python 3.11 or newer. SQLite ships with Python, so no database server is needed.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
source .venv/bin/activate        # macOS / Linux
pip install -r requirements.txt
python run.py                    # creates the database on first start; open http://127.0.0.1:5000
```

## Run it as it is deployed

The pilot runs as a container image behind an HTTPS proxy. With Docker installed:

```bash
cp .env.example .env             # then set the two secrets, see docs/deployment.md
docker compose build
docker compose up -d --wait      # https://localhost, with a certificate from a local authority
docker compose exec app flask create-admin --username researcher
```

## Useful commands

```bash
python -m unittest discover -s tests -t .   # run all tests (or: pip install -r requirements-dev.txt && pytest)
coverage run -m pytest && coverage report   # statement coverage of src/
flask --app src.app analytics               # cohort statistics for the three research questions
flask --app src.app create-admin            # create an administrator, or change the password
flask --app src.app purge-expired --dry-run # what the retention job would delete
flask --app src.app backup-db               # encrypted snapshot (needs PHISHAWARE_BACKUP_KEY)
flask --app src.app restore-db FILE         # replace the database with a snapshot
flask --app src.app run-jobs --once         # one pass of the daily jobs
python scripts/demo_core_logic.py           # core algorithms on inputs you can check by hand
python scripts/inspect_db.py                # privacy-preserving summary of stored data
flask --app src.app reset-db                # development only: delete all participant data
```

Measure a running instance (start from an empty test database; both tools create scripted records):

```bash
python evaluation/loadtest.py --base-url http://127.0.0.1:5000 --levels 1,5,25 \
    --admin-user researcher --admin-password "..."
pip install -r evaluation/requirements.txt && python -m playwright install chromium
python evaluation/browser_audit.py --base-url http://127.0.0.1:5000 \
    --admin-user researcher --admin-password "..."
```

## Project structure

```
PhishAware/
  run.py, wsgi.py         development server; entry point for Gunicorn
  Dockerfile, docker-compose.yml, deploy/Caddyfile, gunicorn.conf.py
                          the deployment: image, services, HTTPS proxy, server settings
  requirements.txt        what the application imports (Flask, cryptography)
  requirements-prod.txt   adds the production server (Gunicorn)
  constraints.txt         exact versions of all eleven packages in the image
  .env.example            every setting, with comments
  src/
    app.py                application factory and start-up checks
    config.py             settings read from environment variables
    db.py, schema.sql     data tier: SQLite connection, schema, seeding, journal mode
    repository.py         all SQL, always parameterized
    modules/              consent (M1), assessment (M2), learning (M3), practice (M4),
                          scoring, results, analytics (M5), survey (M6), admin (M7),
                          security, maintenance (M8), health, assets, display, progress
    templates/            Jinja2 pages (auto-escaped)
    static/               CSS, JavaScript, favicon (no inline code)
  data/                   scenarios.json and lessons.json (content as data)
  tests/                  unit, black-box, data-integrity, and security tests
  evaluation/             loadtest.py (latency, throughput, accuracy) and
                          browser_audit.py (accessibility, layout, page load)
  scripts/                inspect_db.py, demo_core_logic.py, ci_summary.py
  docs/                   deployment, evaluation, security checklist, changelog, release notes
  design/                 Unit 3 design artifacts
  .github/workflows/      CI: quality gate, container build and smoke test, evaluation
```

## Git workflow

The repository follows a Git Flow-style model:

- `main` holds only released versions. Every release has a tag that follows Semantic Versioning, for example `v0.4.0`.
- `develop` is the integration branch for finished work that has passed the tests.
- `feature/<module>-<topic>` branches are short-lived and merge into `develop` with `--no-ff`, so each feature keeps a visible merge commit.
- `fix/<topic>` branches correct defects, and `release/<version>` branches update the version, changelog, and release notes before a release.
- Commits follow Conventional Commits with requirement IDs, for example `feat(M4): add immediate feedback page [FR-06]`.

Cutting a release:

```bash
git checkout -b release/0.4.0 develop        # update the version, changelog, and docs/releases
git checkout main && git merge --no-ff release/0.4.0
git checkout develop && git merge --no-ff release/0.4.0
git push origin main develop release/0.4.0
```

The release is then published on GitHub (Releases, "Draft a new release") with the tag `v0.4.0`, the release
branch as its target, and a summary that points to the full notes in `docs/releases/`. Publishing creates the tag
on the final commit of the release branch, which is the commit that was merged into `main`.

## Security, privacy, and ethics

- No data is stored before consent; the only identifier is a random UUID4.
- No names, email addresses, student numbers, passwords, IP addresses, or demographics are collected. Neither web server writes an access log.
- The consent form states what is stored and for how long: records are deleted 90 days after consent, and encrypted backups 7 days after they are written. A daily job enforces both.
- Withdrawal deletes every linked record at once (`ON DELETE CASCADE`, with deleted rows overwritten in the file).
- Finishing ends the session on the server, so results cannot be opened by the next person at a shared computer.
- The administrator sees aggregates only, and only from five completed participants; the export has no identifier and no timestamp.
- All scenarios are fictional and use reserved `.example` and `.test` domains; links and forms are inert.
- No real phishing emails are ever sent, and no real university systems are contacted.

## Configuration

Every setting is an environment variable. [`docs/deployment.md`](docs/deployment.md) lists all of them; these matter in development:

| Variable | Purpose | Default |
|---|---|---|
| `PHISHAWARE_ENV` | `production` turns on the start-up checks, Secure cookies, and HSTS | `development` |
| `PHISHAWARE_SECRET_KEY` | Session signing key (required in production, at least 32 characters) | random per start |
| `PHISHAWARE_DB` | SQLite file path | `instance/phishaware.db` |
| `PHISHAWARE_BACKUP_KEY` | Key for encrypted backups | none (no backups) |
| `PHISHAWARE_RETENTION_DAYS`, `PHISHAWARE_BACKUP_DAYS` | Retention of records and of backups, in days | `90`, `7` |
| `PORT` | Development server port | `5000` |
| `FLASK_DEBUG` | `1` enables the debugger (refused in production) | off |
