#!/usr/bin/env bash
# Container entrypoint (run by tini as PID 1).
#   LH_DB_MODE=embedded (default): start PostgreSQL inside this container, run the app, stop both cleanly.
#   LH_DB_MODE=cloudsql | external: run the app only; the database lives elsewhere.
# With no arguments the app server starts. Any other command (e.g. "pytest", "alembic ...") runs as the
# app user instead, with the embedded PostgreSQL available. LH_RELOAD=1 restarts the server on code changes.
set -euo pipefail

MODE="${LH_DB_MODE:-embedded}"
APP_USER=app
DB_NAME="${LH_DB_NAME:-looker_helper}"
SOCKET_DIR="${LH_DB_SOCKET_DIR:-/var/run/postgresql}"
export PGDATA="${PGDATA:-/data/postgres}"
PG_BIN="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -n 1)"

log() { echo "[entrypoint] $*"; }
as_user() { local user="$1"; shift; setpriv --reuid="$user" --regid="$user" --init-groups "$@"; }
pg() { as_user postgres "$PG_BIN/$1" "${@:2}"; }
psql_admin() { pg psql -h "$SOCKET_DIR" -U postgres -d postgres -v ON_ERROR_STOP=1 -tA "$@"; }

# App data (encryption key for stored secrets) lives on the volume too
mkdir -p "$LH_DATA_DIR"
chown "$APP_USER:$APP_USER" "$LH_DATA_DIR"
chmod 700 "$LH_DATA_DIR"

start_postgres() {
  mkdir -p "$PGDATA" "$SOCKET_DIR"
  chown postgres:postgres "$PGDATA" "$SOCKET_DIR"
  chmod 700 "$PGDATA"
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    log "Creating the embedded PostgreSQL database in $PGDATA"
    pg initdb -D "$PGDATA" -U postgres --encoding=UTF8 --locale=C.UTF-8 --auth-local=peer --auth-host=reject >/dev/null
  fi
  local have want
  have="$(cat "$PGDATA/PG_VERSION")"
  want="$(basename "$(dirname "$PG_BIN")")"
  if [ "$have" != "$want" ]; then
    log "The data in $PGDATA is from PostgreSQL $have but this image has PostgreSQL $want."
    log "Restore it from a dump instead (see README: Moving the data)."
    exit 1
  fi
  log "Starting PostgreSQL $want (socket only, no network port)"
  pg pg_ctl -D "$PGDATA" -w -t 60 -o "-c listen_addresses='' -c unix_socket_directories='$SOCKET_DIR'" start
  if [ -z "$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = '$APP_USER'")" ]; then
    psql_admin -c "CREATE ROLE \"$APP_USER\" LOGIN" >/dev/null
  fi
  if [ -z "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")" ]; then
    log "Creating database $DB_NAME"
    psql_admin -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$APP_USER\"" >/dev/null
  fi
}

stop_postgres() {
  log "Stopping PostgreSQL"
  pg pg_ctl -D "$PGDATA" -m fast -w -t 60 stop || true
}

cd /app
if [ "$#" -gt 0 ]; then
  args=("$@")
else
  args=(uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="${LH_FORWARDED_ALLOW_IPS:-*}")
  if [ "${LH_RELOAD:-}" = "1" ]; then
    args+=(--reload --reload-dir app --reload-dir static)
  elif [ -n "${LH_WORKERS:-}" ]; then
    args+=(--workers "$LH_WORKERS")
  fi
fi

if [ "$MODE" != "embedded" ]; then
  log "Database mode: $MODE"
  exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups "${args[@]}"
fi

start_postgres
as_user "$APP_USER" "${args[@]}" &
app_pid=$!
trap 'kill -TERM "$app_pid" 2>/dev/null || true' TERM INT

set +e
wait "$app_pid"
status=$?
while kill -0 "$app_pid" 2>/dev/null; do
  wait "$app_pid"
  status=$?
done
set -e

stop_postgres
exit "$status"
