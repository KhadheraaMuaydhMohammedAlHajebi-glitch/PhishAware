# PhishAware production image.
#   docker build -t phishaware:0.6.0 .
# The image holds the application code, its content, and pinned dependencies.
# Secrets and data are supplied when it runs: environment variables and /data.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is rebuilt only when a requirements file changes.
COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

# Application code and content. Tests, evaluation tools, and documents are kept
# out of the image by .dockerignore.
COPY wsgi.py gunicorn.conf.py ./
COPY src ./src
COPY data ./data

# Run as an unprivileged user. /data is the only location it can write to.
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent phishaware \
    && mkdir -p /data/backups \
    && chown -R phishaware:phishaware /data
USER phishaware

ENV PHISHAWARE_ENV=production \
    PHISHAWARE_DB=/data/phishaware.db \
    PHISHAWARE_BACKUP_DIR=/data/backups \
    FLASK_APP=src.app

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
