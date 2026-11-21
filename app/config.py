from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_prefix="LH_", extra="ignore")

    data_dir: Path = BASE_DIR / "data"

    # Database (PostgreSQL only).
    # embedded = PostgreSQL running inside the same container (the Docker image default).
    # external = any PostgreSQL URL (e.g. through the Cloud SQL Auth Proxy). cloudsql = Cloud SQL Python Connector.
    db_mode: Literal["embedded", "external", "cloudsql"] = "embedded"
    database_url: str = ""
    db_name: str = "looker_helper"
    db_user: str = "app"
    db_password: str = ""
    db_socket_dir: str = "/var/run/postgresql"
    db_pool_size: int = 5
    cloudsql_instance: str = ""  # project:region:instance
    cloudsql_ip_type: Literal["private", "public", "psc"] = "private"
    cloudsql_iam_auth: bool = False

    metadata_mode: Literal["demo", "live"] = "demo"
    gcp_billing_project: str | None = None

    cortex_mode: Literal["mock", "real"] = "mock"

    auth_header: str = "X-Goog-Authenticated-User-Email"
    dev_user_email: str | None = "dev.user@example.com"
    admin_emails: str = ""

    secret_key: str | None = None

    currency_format: str = "gbp"
    price_per_tib_gbp: float = 5.0
    pii_access_grant: str = "can_see_pii"
    default_months: int = 12
    large_table_gb: float = 50.0
    cost_warn_gb: float = 50.0
    enable_period_over_period: bool = True

    @property
    def admin_list(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def large_table_bytes(self) -> int:
        return int(self.large_table_gb * 1024**3)


@lru_cache
def get_settings() -> Settings:
    return Settings()
