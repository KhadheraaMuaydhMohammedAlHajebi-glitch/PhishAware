"""Gunicorn settings for the PhishAware container.

Each value that differs between deployments is read from an environment
variable, so one image is tuned without being rebuilt (see docs/deployment.md).
"""

import os

# Inside the container the server listens on every interface so that the reverse
# proxy can reach it. Port 8000 is not published to the host; only the proxy's
# ports 80 and 443 are.
bind = os.environ.get("PHISHAWARE_BIND") or "0.0.0.0:8000"

# Worker processes, and threads in each. SQLite admits one writer at a time, so
# a few threads serve the pilot better than many processes; docs/evaluation.md
# reports the load test behind these defaults.
workers = int(os.environ.get("WEB_CONCURRENCY") or "2")
threads = int(os.environ.get("PHISHAWARE_THREADS") or "4")
worker_class = "gthread"

# Build the application once, before the workers fork. The configuration and
# schema checks then run a single time, and a failed check stops the start-up
# instead of leaving workers that restart in a loop.
preload_app = True

timeout = 30             # restart a worker that has been silent this long (seconds)
graceful_timeout = 30    # time for requests in flight to finish on shutdown
keepalive = 5            # keep an idle connection from the proxy open (seconds)

# Heartbeat files go to memory when it is available: the container's root file
# system is read-only. Bandit reports a fixed temporary directory here (B108).
# The finding does not apply: Gunicorn creates the file with mkstemp, under a
# random name, and no other tenant shares the container's /dev/shm.
SHARED_MEMORY = "/dev/shm"  # nosec B108
worker_tmp_dir = SHARED_MEMORY if os.path.isdir(SHARED_MEMORY) else None
control_socket_disable = True   # no runtime control socket: nothing here needs one

# No access log. Each line would record a client IP address, which the consent
# form promises not to collect (NFR-11). Errors and application events go to
# standard error, where the container platform collects them.
accesslog = None
errorlog = "-"
loglevel = os.environ.get("PHISHAWARE_LOG_LEVEL") or "info"
