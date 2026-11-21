"""Alembic environment: uses the app's own engine, so every LH_DB_MODE (embedded, external, cloudsql) works."""

from alembic import context
from sqlalchemy import text

from app import models  # noqa: F401  (registers tables)
from app.db import Base, get_engine

target_metadata = Base.metadata
MIGRATION_LOCK = 741900  # Postgres advisory lock: only one app instance migrates at a time


def run(connection) -> None:
    connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK})
    try:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK})


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)  # called from app.db.migrate()
else:
    with get_engine().begin() as conn:  # called from the alembic command line
        run(conn)
