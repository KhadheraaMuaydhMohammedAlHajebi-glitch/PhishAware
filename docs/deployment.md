# Deploying PhishAware

This guide takes a new Linux host from nothing to a running pilot instance, and covers operation, upgrade, rollback, and removal. The CI pipeline (`.github/workflows/ci.yml`) performs the same build, start, health check, backup, and restore steps on every push, so the procedure below is tested and not only written down.

## 1. Chosen approach

PhishAware is deployed as one container image, started with Docker Compose on a single host, behind a reverse proxy that provides HTTPS.

| Method considered | Assessment for this project | Decision |
|---|---|---|
| Flask development server (`python run.py`) | Single process, no HTTPS, and Flask's documentation says not to use it in production. | Development only |
| Python virtual environment with Gunicorn and a hand-configured web server on a virtual machine | Works, but every step is manual, so the pilot host can drift from what was tested. | Rejected |
| Platform as a service | Simple to start. The instance file system is usually temporary, which does not suit a SQLite file, and the research data would sit on a third party's platform under its terms. | Rejected |
| **Container image with Docker Compose on one host** | The image that CI tested is the image that runs. Settings, services, and hardening are declared in files under version control. One host is enough for a pilot of at most 50 participants. | **Selected** |
| Container orchestration (Kubernetes) | Solves multi-host scheduling and scaling, which this project does not need, at a large cost in complexity. | Rejected |

**Release strategy.** A new version replaces the old one in a single step (a basic, or "recreate", deployment) in a quiet period between pilot sessions, and takes about ten seconds. Rolling, blue-green, and canary releases keep two versions running side by side. They need several instances behind a load balancer and a database that both versions can share; SQLite on one host provides neither, and a pilot of this size does not justify them. Rollback is covered by keeping the previous image and an encrypted backup (section 8).

## 2. What runs

```
                       host (Linux, Docker Engine)
 browser ──HTTPS 443──▶ proxy (Caddy 2)  ──HTTP 8000──▶ app (Gunicorn, 2 workers x 4 threads)
          HTTP 80 is     TLS certificate,                 Flask application
          redirected     compression,                        │
                         no access log                       ▼
                                                    /data/phishaware.db   (volume phishaware-data)
                                                    /data/backups/*.db.enc
                                                             ▲
                                           jobs (same image: flask run-jobs)
                                           once a day: delete expired records,
                                           write an encrypted backup, delete old backups
```

| Service | Image | Role |
|---|---|---|
| `app` | `phishaware:<version>` (built from `Dockerfile`) | The web application, served by Gunicorn. Only the proxy can reach it. |
| `jobs` | the same image | The data-protection jobs (section 7). |
| `proxy` | `caddy:2-alpine` | Terminates HTTPS, obtains and renews the certificate, redirects HTTP to HTTPS. |

Both PhishAware containers run as an unprivileged user (uid 10001) with a read-only root file system, no Linux capabilities, and `no-new-privileges`. The only place they can write is the `/data` volume.

## 3. Prerequisites

| Item | Requirement |
|---|---|
| Operating system | 64-bit Linux. Tested on Ubuntu 24.04 LTS. |
| Container runtime | Docker Engine with the Compose plugin. Tested with Docker Engine 28.0 and Compose 2.38. Docker Engine 25 or newer is recommended: older engines run the first health check later, so start-up takes longer. |
| Hardware | 1 CPU core and 1 GB of memory are enough for the pilot; `docs/evaluation.md` reports the measured memory use. |
| Storage | 1 GB free. **Turn on disk encryption for the volume that holds Docker's data** (for example LUKS, or the provider's encrypted disks). The application encrypts its backups itself; the live database file relies on this disk encryption. |
| Network | Inbound TCP 80 and 443. A DNS name that points to the host, so that the proxy can obtain a public certificate. |
| Clock | Synchronised (NTP). Retention and session limits are computed from the host clock. |
| Tools on the host | `git`, and `python3` to generate the two secrets. |

Nothing else is installed on the host. Python 3.13, Flask, Gunicorn, the cryptography library, and SQLite are inside the image. `requirements.txt` and `requirements-prod.txt` name the three packages PhishAware uses directly, and `constraints.txt` fixes the version of every package in the image (eleven in total). CI fails if the image contains anything else.

For development without Docker, Python 3.11 or newer is enough (CI tests 3.11 and 3.13); see the README.

## 4. Settings

Every setting is an environment variable. Compose reads them from the file `.env`, which is never committed and is excluded from the image. `.env.example` is the template.

| Variable | Purpose | Default | Required |
|---|---|---|---|
| `PHISHAWARE_ENV` | `production` turns on the start-up checks, Secure cookies, and HSTS. | `development` (the image sets `production`) | |
| `PHISHAWARE_SECRET_KEY` | Signs session cookies. At least 32 characters. | none | Yes |
| `PHISHAWARE_BACKUP_KEY` | 32 random bytes in URL-safe base64; encrypts backups. Keep a copy away from the server. | none | For backups |
| `PHISHAWARE_DOMAIN` | Host name the proxy serves and obtains a certificate for. | `localhost` | Yes, for a public host |
| `PHISHAWARE_CONTACT` | Person to ask about the study, shown on the consent page. | not shown | Recommended |
| `PHISHAWARE_RETENTION_DAYS` | Days after consent before a participant's records are deleted. | `90` | |
| `PHISHAWARE_BACKUP_DAYS` | Days before a backup is deleted. | `7` | |
| `PHISHAWARE_JOB_INTERVAL` | Seconds between passes of the jobs service (at least 60). | `86400` | |
| `PHISHAWARE_SQLITE_JOURNAL` | `AUTO`, `WAL`, or `DELETE`. `AUTO` uses write-ahead logging only when the SQLite library contains the fix for the WAL-reset bug. | `AUTO` | |
| `WEB_CONCURRENCY`, `PHISHAWARE_THREADS` | Gunicorn worker processes, and threads in each. | `2`, `4` | |
| `PHISHAWARE_LOG_LEVEL` | Gunicorn's log level. | `info` | |
| `PHISHAWARE_HTTP_PORT`, `PHISHAWARE_HTTPS_PORT` | Host ports published by the proxy. | `80`, `443` | |
| `PHISHAWARE_DB`, `PHISHAWARE_BACKUP_DIR` | Database file and backup folder. | `/data/phishaware.db`, `/data/backups` in the image | |
| `PHISHAWARE_COOKIE_SECURE` | `0` allows cookies over plain HTTP, for a local smoke test only. | on in production | |

The consent page states the retention period and the backup period. Set both before the first participant agrees and leave them unchanged during a study.

**Fail-fast checks.** The application refuses to start, and names the setting, when `PHISHAWARE_ENV` or `PHISHAWARE_SQLITE_JOURNAL` is misspelled, when a period is not a whole number or is below its minimum, or, in production, when the secret key is missing or shorter than 32 characters or debug mode is on.

## 5. Install and start

```bash
# 1. Get the release
git clone https://github.com/KhadheraaMuaydhMohammedAlHajebi-glitch/PhishAware.git
cd PhishAware
git checkout v0.6.0

# 2. Create the settings file and generate the two secrets
cp .env.example .env
chmod 600 .env
python3 -c "import secrets; print(secrets.token_hex(32))"                                 # PHISHAWARE_SECRET_KEY
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # PHISHAWARE_BACKUP_KEY
#    Paste both values into .env, set PHISHAWARE_DOMAIN and PHISHAWARE_CONTACT,
#    and store a copy of the backup key somewhere other than this server.

# 3. Build the image and start the three services
docker compose build
docker compose up -d --wait

# 4. Create the administrator account (the password is typed twice and never shown)
docker compose exec app flask create-admin --username researcher
```

`docker compose up -d --wait` returns when the application answers its health check and the jobs service has completed its first pass.

## 6. Verify

```bash
docker compose ps                                   # app and jobs "healthy", proxy "running"
curl https://<your-domain>/healthz                  # {"scenarios":30,"status":"ok","version":"0.6.0"}
curl -I http://<your-domain>/consent                # 308 redirect to HTTPS
docker compose logs jobs                            # the first maintenance pass and its backup
```

Then open `https://<your-domain>/` in a browser, work through a session, and sign in at `/admin`. Use **Withdraw** in the test session instead of **Finish**, or it is counted with the pilot data.

## 7. Operate

| Task | Command |
|---|---|
| State of the services | `docker compose ps` |
| Application and server messages | `docker compose logs app` |
| Jobs: what was deleted and backed up, and when | `docker compose logs jobs` |
| Is the retention job alive? | `docker compose exec jobs flask jobs-status` |
| Cohort statistics in the terminal | `docker compose exec app flask analytics` |
| Create an administrator, or change a password | `docker compose exec app flask create-admin --username <name>` |
| Back up now | `docker compose exec app flask backup-db` |
| See what the retention job would delete | `docker compose exec app flask purge-expired --dry-run` |
| Copy the backups off the host | `docker compose cp app:/data/backups ./backups` |
| Stop / start | `docker compose stop` / `docker compose up -d --wait` |

**The jobs service** runs `flask run-jobs`. On start, and then every `PHISHAWARE_JOB_INTERVAL` seconds, it:

1. deletes every participant whose consent is older than `PHISHAWARE_RETENTION_DAYS`, with all linked records;
2. writes an encrypted snapshot to `/data/backups` (skipped, with a message, when no backup key is set);
3. deletes backups older than `PHISHAWARE_BACKUP_DAYS`.

The order keeps an expired record out of the new snapshot. A pass that fails is reported in the log and tried again after five minutes. The container is reported as unhealthy when no pass has succeeded within the interval plus fifteen minutes.

**Logs.** Neither Gunicorn nor Caddy writes an access log, because each line would record a client IP address, which the consent form promises not to collect. Application events (consent recorded, assessment completed, sign-in failed) are logged without identifiers.

**Backups** are consistent snapshots taken while the system is in use, encrypted with AES-256 in Galois/Counter Mode before they are written, and readable only by the application user. The encryption is authenticated, so a file that was altered or cut short is rejected. A backup cannot be restored without `PHISHAWARE_BACKUP_KEY`.

## 8. Restore, upgrade, and roll back

**Restore a backup.** The web service and the jobs service are stopped so that nothing writes during the copy.

```bash
docker compose exec app sh -c 'ls /data/backups'                       # choose a snapshot
docker compose stop app jobs
docker compose run --rm --no-deps app flask restore-db /data/backups/phishaware-<time>.db.enc --yes
docker compose up -d --wait
```

The database is replaced only after the snapshot has passed three checks: authenticity (the AES-GCM tag), SQLite's integrity check, and a schema check. A restore brings back the state at the time of the snapshot. Two consequences follow. Sessions recorded after the snapshot are lost. A participant who withdrew after the snapshot is restored with it, and cannot be identified to be deleted again; with daily snapshots that window is at most one day, and the record still expires with the retention period. Restore only when the alternative is losing the data set.

**Upgrade.**

```bash
docker compose exec app flask backup-db          # a snapshot of the current state
git fetch --tags && git checkout v0.6.1          # the new release
docker compose build
docker compose up -d --wait                      # recreates app and jobs; about ten seconds
curl https://<your-domain>/healthz               # reports the new version
```

On start, the new version adds any table it introduces (`CREATE TABLE IF NOT EXISTS`); existing data is not changed.

**Roll back.** Check out the previous tag, build, and start again. Images are tagged with their version, so the previous image is still on the host. If the newer version has changed data in a way the older one cannot read, restore the snapshot taken before the upgrade.

```bash
git checkout v0.6.0
docker compose build && docker compose up -d --wait
```

## 9. End of the pilot

```bash
docker compose exec app flask analytics                       # final aggregate statistics
#    Download the de-identified export from /admin while signed in.
docker compose down --volumes                                 # removes the containers, the database, and every backup
```

`down --volumes` deletes everything at once, which is earlier than the retention period requires. Delete any copies of backups that were moved off the host as well.

## 10. Configuration management

| Activity | How PhishAware does it |
|---|---|
| Identification | Everything that defines a deployment is a file under version control: `Dockerfile`, `docker-compose.yml`, `deploy/Caddyfile`, `gunicorn.conf.py`, `requirements*.txt`, `constraints.txt`, `src/schema.sql`, `.env.example`. Secrets and data are deliberately outside: `.env` and the `/data` volume. |
| Baselines | Each release is a Git tag (`v0.6.0`) and an image with the same version. The tag fixes the code, the content, the settings template, and the version of every dependency. |
| Change control | Changes arrive on `feature/` or `fix/` branches and merge only when the CI pipeline passes: lint, tests with a coverage threshold, security scans, an image build, a smoke test of the running stack, and the evaluation job. |
| Status accounting | `docs/CHANGELOG.md`, the release notes in `docs/releases/`, and `/healthz`, which reports the version that is running. |
| Audit | CI compares the packages inside the built image with `constraints.txt`, starts the image without a secret key to confirm that it refuses, and rehearses the restore procedure. |

Because the same image is promoted from CI to the pilot and only `.env` differs, a setting cannot be changed on the server without leaving a trace in a file, and a deployment can be rebuilt from the tag at any time.
