# Maintenance plan

This plan says how PhishAware is kept working, safe, and useful after release 0.7.0: what is watched, how often, how a defect or a request becomes a release, which tools support that, and what is done when something goes wrong. It complements the deployment guide (`docs/deployment.md`), which has the commands, and the test plan (`docs/test-plan.md`), which says how a change is verified.

One person develops, operates, and maintains the system. The plan is written for that situation: whatever can be checked by the pipeline is checked there on every change and once a week, and the manual tasks are few and have a fixed interval.

## 1. The four types of maintenance

| Type | What it means for PhishAware | Examples from this project | How it is handled |
|---|---|---|---|
| **Corrective**: remove a defect that was found | A wrong result, an error page, lost or exposed data, a failed job | Release 0.7.0 corrects six defects that system testing found, among them a status 500 for a token with characters outside ASCII (D-1) and a database of an earlier release that could not hold an administrator (D-5) | A `fix/` branch with a test that fails before the correction; a patch release; time limits by severity (section 2) |
| **Adaptive**: keep working while the environment changes | New releases of Python, Flask, Werkzeug, Gunicorn, the cryptography library, SQLite, the base image, Docker, Caddy, and the browsers; changes of the hosting service and of GitHub Actions | Werkzeug 3.1.9 stopped limiting the size of a form, which left the application without any limit (D-6). SQLite 3.51.3 corrects the defect that makes write-ahead logging unsafe, and the journal setting `AUTO` adopts it without a change to the code. Docker Hub refused anonymous downloads from the shared runners (status 429, runs 33 and 34), so the pipeline now fetches its base images from two mirrors first | All eleven packages are pinned, so nothing changes unnoticed; a version is raised on a branch, and the whole pipeline, in three browser engines, decides whether the change is safe |
| **Perfective**: make it better for its users | Usability findings, requests from participants and from the researcher, new content, faster pages | Release 0.7.0 corrects the six findings of the usability inspections (U-1 to U-6). After the pilot, the answers to the built-in usability survey and the error rates by cue will show where to improve | A `feature/` branch and a minor release; a change to what is stored or to the consent text needs approval first (section 3) |
| **Preventive**: lower the chance of a future failure | Tests, analysis, rehearsals, and structure that make later changes safe | 296 automated tests that execute every statement of the application; static analysis and a dependency audit on every push; the restore, the upgrade, and the rollback are rehearsed by the pipeline; the database records its schema version, and changes to it are explicit steps | Part of the definition of done: a change is not merged without its tests, and a release is not made while the pipeline fails |

## 2. Monitoring, updates, and performance checks

**Automatic, without anybody watching**

| What | How | When |
|---|---|---|
| The web service answers | Docker's health check requests `/healthz` and shows the result in `docker compose ps`; a service that crashes is restarted at once (system case ST-17 kills it in the middle of a journey: it answered again after one to two seconds, and no answer was lost) | Every 30 seconds |
| The retention job is alive | The jobs service records each successful pass; `flask jobs-status` fails when none succeeded within a day and 15 minutes, and Docker then shows the service as unhealthy | Every 5 minutes |
| Expired records, old sign-in records, and old backups are deleted; a new encrypted backup is written | `flask run-jobs` in the jobs service | Once a day, and at every start |
| The HTTPS certificate is renewed | The proxy (Caddy) | When about a third of its lifetime is left |
| Known vulnerabilities in the pinned packages | `pip-audit` in the pipeline | Every push, and every Monday on the main branch |
| Nothing has broken: 296 tests, the image build, the smoke test, the restore, upgrade, and rollback rehearsals, the system and acceptance cases in three browser engines | The pipeline (five jobs) | Every push, and every Monday |
| Performance: response times at 1 to 200 concurrent participants, page load on a slow connection, and 25 participants at a human pace (three minutes on a branch, ten on a release and in the weekly run) | Load test, browser audit, and endurance test in the pipeline; the pipeline fails when the budget of NFR-01 is missed | Every push, and every Monday |

The weekly run exists for the weeks without a push. It rebuilds the image from the current base image and audits the dependencies again, so a new advisory, a changed base image, or a new browser release is noticed within a week.

**By the operator**

| Task | Command or place | When |
|---|---|---|
| Look at the state of the services | `docker compose ps` | Daily while a study runs |
| Read the application and job logs for errors, blocked sign-ins, and failed passes | `docker compose logs app jobs` | Daily while a study runs; weekly otherwise |
| Look at the result of the weekly pipeline run, and keep it switched on | The Actions page of the repository. GitHub notifies the account that set the schedule when a scheduled run fails. It switches the schedule of a public repository off after 60 days without activity in the repository; the same page switches it on again | Weekly |
| Copy the encrypted backups to another machine | `docker compose cp app:/data/backups ./backups`, then copy the folder away | Weekly while a study runs. The backups are on the same disk as the database, so without this copy a lost host loses both |
| Restore the newest backup on a second machine | `docs/deployment.md`, section 8 | Before a study, and once a term |
| Measure the host: the load test and the endurance test | `docs/test-plan.md`, section 8 | Before a study, and after any change of the host |
| Install security updates of the operating system and of Docker | The host's package manager | Monthly; at once for a critical advisory |
| Review the dependencies and the base image for new releases | `constraints.txt`, `Dockerfile` | At the start of each release |
| Review the scenarios and lessons: do they still look like current phishing? | `data/scenarios.json`, `data/lessons.json` | Once a term, never during a study |

**What starts an update**

| Trigger | Limit |
|---|---|
| Advisory rated critical or high for code that the application uses; defect of severity critical | The service is corrected or taken offline within 24 hours of the report; a corrected release follows within 7 days |
| Defect of severity high | 7 days |
| Advisory or defect of severity medium | 30 days |
| Low severity, and every perfective change | The next planned release |
| A component reaches the end of its support (Python 3.11 in October 2027, Python 3.13 in October 2029) | A release before that date |

The severities are defined in `docs/test-plan.md`, section 5. The limits for advisories are those of `docs/security-checklist.md`, section 6.4.

## 3. Versions, corrections, and requests

**Where a report arrives.** Participants see the researcher's contact line on every page. Everything else is recorded as an issue in the repository, labelled `bug`, `enhancement`, `security`, or `content`. A suspected vulnerability is reported privately to the researcher and gets a public issue only when a corrected release exists.

**From report to release.**

1. Triage: reproduce it, give it a severity, and decide whether it is a defect (the system does not do what the requirements say) or a request (the requirements would have to change).
2. A branch from `develop`: `fix/<topic>` or `feature/<topic>`.
3. A test that fails for the reported reason, then the change that makes it pass.
4. The pipeline on the branch. Only a branch that passes is merged into `develop`, with a merge commit that keeps the branch visible.
5. A `release/<version>` branch: version number, change log, release notes, and the documents that the change affects.
6. Merge into `main`, publish the tag as a GitHub Release, deploy as the guide describes (backup, build, start, health check), and close the issue with the number of the release.

An urgent correction of the running release takes the same steps on a `fix/` branch that starts from `main`, and is merged into `main` and `develop`.

**Version numbers** follow Semantic Versioning. A correction raises the third number (0.7.1), a new function the second (0.8.0). The first number changes when an older release can no longer read the data, and the release notes then say how to migrate.

**What a request must pass.** A request is weighed against the requirements baseline and the limits of the study. A change that affects what is stored, how long it is kept, or what the consent page says needs the approval of the instructor or review board first, a new consent version (the version is stored with every consent), and updated tests of the promises on that page. A change to a scenario, to the scoring, or to the order of the steps is not deployed while a cohort is taking part, because both assessment forms must stay the same for everybody in it. Scenario texts are stored in the database when it is created; after a release that changes them, `flask init-db` updates them and keeps every record.

**Data across versions.** The database records its schema version (`PRAGMA user_version`; this release writes 2). A release that changes an existing table adds a migration step in `src/db.py` and a test that opens a database built with the schema file of the previous release (`tests/fixtures`). The pipeline rehearses the upgrade from the previous release, the rollback, and the upgrade again, and compares the statistics each time. The backup format has its own marker (`PHISHAWARE-BACKUP-1`), which is authenticated with the data.

## 4. Version control and configuration management

| Tool | What it contributes to maintenance |
|---|---|
| Git and GitHub, Git Flow branches | Every change is a separate unit with a stated reason. Commit messages name the requirement and the defect, so the history answers why a line exists. A harmful change is found with `git bisect` and undone with `git revert` |
| Tags and GitHub Releases | A baseline for every release: code, content, settings template, and the version of every dependency. Any release can be rebuilt, and the previous one is the rollback target |
| `constraints.txt`, pinned runner image, pinned action versions | The environment changes only when a commit changes it, so an adaptive change can be tested like any other |
| GitHub Actions | Change control: no merge without the five jobs. It also audits the configuration: the image must hold exactly the pinned packages, must refuse to start without a secret key, and must serve TLS 1.2 and 1.3 only |
| Docker image and Compose file | The artefact that was tested is the artefact that runs; services, limits, and hardening are declared in a file under version control |
| `.env` outside the repository, `.env.example` inside | Secrets never enter the history; every setting is documented where it is defined; the application refuses an unsafe setting when it starts |
| `/healthz`, change log, release notes | Status accounting: which version is running, and what changed in it |
| Schema version and migration steps | The configuration of the data, under the same control as the code |

## 5. Risks after deployment

| Risk | Prevention | How it shows | What is done |
|---|---|---|---|
| The database is lost or damaged (disk failure, a faulty upgrade) | Daily encrypted backups kept for 7 days; a backup before every upgrade; weekly copy to another machine; restore rehearsed by the pipeline | Health check fails; integrity check fails on restore | Restore the newest backup (`docs/deployment.md`, section 8). At most one day of sessions is lost. A participant who withdrew after the snapshot returns with it and expires with the retention period |
| An upgrade fails | Upgrade and rollback rehearsed by the pipeline; migration steps run in one transaction and leave the database unchanged if they fail | The service does not become healthy | Start the previous image; if needed, restore the backup taken before the upgrade |
| A vulnerability is published for a dependency | Pinned versions; weekly audit; few dependencies (eleven packages) | The pipeline fails | Raise the version on a `fix/` branch within the limits of section 2; if no correction exists and the affected code is used, take the service offline |
| A new version of a dependency changes behaviour | Pinned versions; hostile-input and size-limit cases in the pipeline | A case fails on the branch that raises the version | Adapt the code before the version is adopted, as with Werkzeug 3.1.9 |
| The pilot host is slower than the runners | Load test on the host before a study | 95th percentile above the budget | A larger host; write-ahead logging once the image has SQLite 3.51.3; smaller groups at a time |
| The session key or the backup key leaks or is lost | Keys only in `.env` (readable by its owner) and, for the backup key, one copy off the host | Suspicion, or a backup that cannot be decrypted | Leak: replace the key between sessions (a new session key ends every open session), delete older backups. Loss of the backup key: the database itself is unaffected; set a new key and take a backup at once |
| The administrator cannot sign in (forgotten password, or the rate limit triggered by somebody else's guesses) | Limit of five failures in 15 minutes for a name and fifty in total; no account names are revealed | Sign-in page reports too many attempts | The block ends after 15 minutes; `flask create-admin` on the host sets a new password. The results are also available on the host with `flask analytics` |
| The retention job stops, and records are kept longer than promised | Health check of the jobs service; failed passes are tried again after five minutes | Service shown as unhealthy; no new line in its log | Read the log, correct the cause, and run `flask purge-expired` by hand |
| The certificate is not renewed | Automatic renewal; ports 80 and 443 open | Browsers refuse the site | Check DNS and the ports; restart the proxy. The site stays unavailable until then, because browsers have been told to refuse plain HTTP |
| The host is compromised | Unprivileged containers with a read-only file system; no personal identifiers stored; encrypted backups; disk encryption | Unexpected processes or log entries | Take the service offline, keep the evidence, replace both keys, rebuild the host from the release, and inform the instructor and, where the institution requires it, its review board |
| The only maintainer is unavailable | Every procedure is in the repository and is run by the pipeline; settings are documented in `.env.example` | | A second person with access to the host and the backup key can operate and restore the system from the documents alone |
| The pipeline itself fails for reasons outside the code (rate limits, slow or stalled runners) | Mirrors for base images; time limits and retries; the weekly run shows such failures early | A red run without a change in the code | Repeat the failed job; if it recurs, treat it as a defect of the pipeline |

## 6. Review of this plan

The plan is reviewed with every minor release and after every incident: was the problem noticed by what section 2 watches, and was it handled within the limits of section 2? If not, the plan or the pipeline is changed in the same release.
