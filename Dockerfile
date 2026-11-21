# Looker Helper: the app plus an embedded PostgreSQL in one image.
# Switch to Cloud SQL (or any external PostgreSQL) with LH_DB_MODE; see README.
#
# Targets:  production (default) - what gets deployed
#           test                 - production + pytest and the tests (docker compose run --rm tests)
#
# Corporate builds: point BASE_IMAGE at your internal registry mirror, and set PIP_INDEX_URL / PIP_TRUSTED_HOST
# build arguments if PyPI is reached through a proxy, e.g.
#   docker build --build-arg BASE_IMAGE=registry.example.com/python:3.12-slim-bookworm --build-arg PIP_INDEX_URL=... .
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE} AS runtime

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    LH_DB_MODE=embedded \
    LH_DATA_DIR=/data/app \
    PGDATA=/data/postgres \
    PORT=8000

# PostgreSQL server (without Debian's default cluster; the entrypoint creates ours on the /data volume) and tini as PID 1
RUN mkdir -p /etc/postgresql-common \
 && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv && pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY static ./static
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh \
 && mkdir -p /data \
 && chown app:app /app

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1

# Starts as root only to prepare /data and start PostgreSQL as the postgres user; the app itself runs as "app".
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]


FROM runtime AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY tests ./tests
# Lets the database tests wipe and rebuild the schema. Only ever set in this throwaway test image.
ENV LH_TEST_ALLOW_RESET=1
HEALTHCHECK NONE
CMD ["pytest", "-q", "-p", "no:cacheprovider"]


FROM runtime AS production
