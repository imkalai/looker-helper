"""Looker connections: personal ones, and shared ones that admins create for everyone."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.models import LookerConnection
from app.security import decrypt, encrypt
from app.services.looker_api import test_connection

router = APIRouter(prefix="/api/connections", tags=["looker"])


def _out(c: LookerConnection, current: CurrentUser) -> dict:
    return {
        "id": c.id, "name": c.name, "base_url": c.base_url, "bq_connection_name": c.bq_connection_name,
        "looker_project": c.looker_project, "folder_layout": c.folder_layout, "client_id": c.client_id,
        "has_secret": bool(c.client_secret_enc), "shared": c.owner_id is None,
        "can_edit": c.owner_id == current.id or (c.owner_id is None and current.is_admin),
        "is_default": current.user.preferences.get("default_connection_id") == c.id,
        "last_test_status": c.last_test_status, "last_test_message": c.last_test_message,
        "last_tested_at": c.last_tested_at.isoformat() if c.last_tested_at else None,
    }


def _get(db: Session, current: CurrentUser, conn_id: int, edit: bool = False) -> LookerConnection:
    c = db.get(LookerConnection, conn_id)
    if not c or c.owner_id not in (None, current.id):
        raise HTTPException(404, "That connection wasn't found.")
    if edit and not (c.owner_id == current.id or current.is_admin):
        raise HTTPException(403, "Only an administrator can change a shared connection.")
    return c


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=8, max_length=500)
    bq_connection_name: str = Field(min_length=1, max_length=200)
    looker_project: str = ""
    folder_layout: Literal["standard", "flat"] = "standard"
    client_id: str = ""
    client_secret: str | None = None  # None = keep the stored secret
    shared: bool = False
    make_default: bool = False


def _apply(c: LookerConnection, body: ConnectionIn) -> None:
    c.name, c.base_url = body.name.strip(), body.base_url.strip().rstrip("/")
    c.bq_connection_name, c.looker_project = body.bq_connection_name.strip(), body.looker_project.strip()
    c.folder_layout, c.client_id = body.folder_layout, body.client_id.strip()
    if body.client_secret is not None:
        c.client_secret_enc = encrypt(body.client_secret)


def _set_default(db: Session, current: CurrentUser, conn_id: int) -> None:
    current.user.preferences = {**current.user.preferences, "default_connection_id": conn_id}
    db.add(current.user)


def _run_test(c: LookerConnection) -> None:
    c.last_test_status, c.last_test_message = test_connection(c.base_url, c.client_id, decrypt(c.client_secret_enc), c.bq_connection_name)
    c.last_tested_at = datetime.now(timezone.utc)


@router.get("")
def list_connections(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(LookerConnection).where(or_(LookerConnection.owner_id == current.id, LookerConnection.owner_id.is_(None))).order_by(LookerConnection.name))
    return [_out(c, current) for c in rows]


@router.post("")
def create_connection(body: ConnectionIn, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    if body.shared and not current.is_admin:
        raise HTTPException(403, "Only an administrator can create shared connections.")
    c = LookerConnection(owner_id=None if body.shared else current.id)
    _apply(c, body)
    db.add(c)
    db.flush()
    _run_test(c)
    first = not db.scalars(select(LookerConnection.id).where(LookerConnection.owner_id == current.id, LookerConnection.id != c.id)).first()
    if body.make_default or (first and not current.user.preferences.get("default_connection_id")):
        _set_default(db, current, c.id)
    db.commit()
    return _out(c, current)


@router.put("/{conn_id}")
def update_connection(conn_id: int, body: ConnectionIn, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    c = _get(db, current, conn_id, edit=True)
    _apply(c, body)
    if body.make_default:
        _set_default(db, current, c.id)
    db.commit()
    return _out(c, current)


@router.post("/{conn_id}/default")
def make_default(conn_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    c = _get(db, current, conn_id)
    _set_default(db, current, c.id)
    db.commit()
    return _out(c, current)


@router.delete("/{conn_id}")
def delete_connection(conn_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    c = _get(db, current, conn_id, edit=True)
    db.delete(c)
    if current.user.preferences.get("default_connection_id") == conn_id:
        current.user.preferences = {k: v for k, v in current.user.preferences.items() if k != "default_connection_id"}
    db.commit()
    return {"ok": True}


@router.post("/{conn_id}/test")
def test_saved(conn_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    c = _get(db, current, conn_id)
    _run_test(c)
    db.commit()
    return _out(c, current)


@router.post("/test")
def test_unsaved(body: ConnectionIn, _: CurrentUser = Depends(get_current_user)):
    status, message = test_connection(body.base_url, body.client_id, body.client_secret or "", body.bq_connection_name)
    return {"status": status, "message": message}
