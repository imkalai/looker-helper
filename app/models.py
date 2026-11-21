from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSONB


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    preferences: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LookerConnection(Base):
    __tablename__ = "looker_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)  # None = shared by admin
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    bq_connection_name: Mapped[str] = mapped_column(String(200))
    looker_project: Mapped[str] = mapped_column(String(200), default="")
    folder_layout: Mapped[str] = mapped_column(String(20), default="standard")  # standard | flat
    client_id: Mapped[str] = mapped_column(String(200), default="")
    client_secret_enc: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_status: Mapped[str] = mapped_column(String(30), default="untested")  # untested | ok | failed
    last_test_message: Mapped[str] = mapped_column(Text, default="")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    gcp_project: Mapped[str] = mapped_column(String(200))
    dataset: Mapped[str] = mapped_column(String(200))
    dataset_label: Mapped[str] = mapped_column(String(200), default="")
    tables: Mapped[list] = mapped_column(JsonType, default=list)
    looker_connection_id: Mapped[int | None] = mapped_column(ForeignKey("looker_connections.id"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | ready | review
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    snapshots: Mapped[list["TableSnapshot"]] = relationship(cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(cascade="all, delete-orphan", order_by="Message.id")
    packages: Mapped[list["Package"]] = relationship(cascade="all, delete-orphan", order_by="Package.version")
    connection: Mapped[LookerConnection | None] = relationship()


class TableSnapshot(Base):
    """Table details captured when the session starts. The session only ever uses these tables."""

    __tablename__ = "table_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    table_name: Mapped[str] = mapped_column(String(300))
    meta: Mapped[dict] = mapped_column(JsonType)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    kind: Mapped[str] = mapped_column(String(20), default="text")  # text | plan | package | error
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Package(Base):
    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    plan: Mapped[dict] = mapped_column(JsonType)
    summary: Mapped[dict] = mapped_column(JsonType, default=dict)
    checks: Mapped[list] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    files: Mapped[list["PackageFile"]] = relationship(cascade="all, delete-orphan", order_by="PackageFile.id")


class PackageFile(Base):
    __tablename__ = "package_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"), index=True)
    path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20))  # view | model | dashboard
    content: Mapped[str] = mapped_column(Text)


class AppSetting(Base):
    """Admin-level key/value settings (e.g. the Cortex configuration)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonType, default=dict)
