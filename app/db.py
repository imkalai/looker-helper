"""Database engine for every deployment mode, and schema migrations (Alembic) at startup."""

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import BASE_DIR, Settings, get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_connector = None  # Cloud SQL connector, closed on shutdown


def _postgres_url(url: str) -> str:
    """Accept postgres:// and postgresql:// URLs and use the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(settings: Settings) -> Engine:
    global _connector
    mode = settings.db_mode
    pool = {"pool_pre_ping": True, "pool_size": settings.db_pool_size, "max_overflow": settings.db_pool_size}

    if mode == "embedded":
        # Unix socket only, peer authentication: the container's OS user "app" maps to the database role "app".
        # No password and no TCP port. docker/entrypoint.sh creates the role and database.
        url = URL.create("postgresql+psycopg", username="app", database=settings.db_name,
                         query={"host": settings.db_socket_dir})
        return create_engine(url, **pool)

    if mode == "external":
        if not settings.database_url:
            raise RuntimeError("LH_DB_MODE=external needs LH_DATABASE_URL, e.g. postgresql://user:pass@127.0.0.1:5432/looker_helper")
        return create_engine(_postgres_url(settings.database_url), **pool)

    if mode == "cloudsql":
        if not settings.cloudsql_instance:
            raise RuntimeError("LH_DB_MODE=cloudsql needs LH_CLOUDSQL_INSTANCE (project:region:instance)")
        from google.cloud.sql.connector import Connector, IPTypes

        ip = {"private": IPTypes.PRIVATE, "public": IPTypes.PUBLIC, "psc": IPTypes.PSC}[settings.cloudsql_ip_type]
        _connector = Connector(ip_type=ip, enable_iam_auth=settings.cloudsql_iam_auth, refresh_strategy="lazy")

        def connect():
            return _connector.connect(
                settings.cloudsql_instance, "pg8000", user=settings.db_user, db=settings.db_name,
                password=None if settings.cloudsql_iam_auth else settings.db_password,
            )

        return create_engine("postgresql+pg8000://", creator=connect, **pool)

    raise RuntimeError(f"Unknown LH_DB_MODE '{mode}'")


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = make_engine(get_settings())
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def migrate() -> None:
    """Bring the schema up to date. Safe with several app instances: Postgres runs migrations under a lock."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(BASE_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BASE_DIR) / "migrations"))
    with get_engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def init_db() -> None:
    from app import models  # noqa: F401  (registers tables)

    get_engine()
    migrate()
    log.info("Database ready (%s)", get_settings().db_mode)


def check_db() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        log.warning("Database check failed", exc_info=True)
        return False


def reset_engine() -> None:
    """Dispose the engine (shutdown, and tests pointing the app at a fresh database)."""
    global _engine, _SessionLocal, _connector
    if _engine is not None:
        _engine.dispose()
    if _connector is not None:
        _connector.close()
    _engine = _SessionLocal = _connector = None


def get_db() -> Iterator[Session]:
    if _SessionLocal is None:
        get_engine()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
