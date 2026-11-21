"""Sessions, chat turns, packages and downloads. Every query is scoped to the signed-in user."""

import difflib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.models import ChatSession, LookerConnection, Message, Package, TableSnapshot
from app.services import chat
from app.services.builder import package_card
from app.services.metadata import get_metadata_provider
from app.services.packaging import build_zip
from app.services.text import humanize, snake

router = APIRouter(prefix="/api", tags=["sessions"])


def _session(db: Session, current: CurrentUser, session_id: int) -> ChatSession:
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != current.id:
        raise HTTPException(404, "That session wasn't found.")
    return s


def _package(db: Session, current: CurrentUser, package_id: int) -> Package:
    p = db.get(Package, package_id)
    if not p:
        raise HTTPException(404, "That package wasn't found.")
    _session(db, current, p.session_id)
    return p


def _message_out(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "kind": m.kind, "content": m.content, "payload": m.payload, "created_at": m.created_at.isoformat()}


def _session_out(s: ChatSession, full: bool = False) -> dict:
    latest = s.packages[-1] if s.packages else None
    conn = s.connection
    out = {
        "id": s.id, "title": s.title, "status": s.status,
        "project": s.gcp_project, "dataset": s.dataset, "dataset_label": s.dataset_label or humanize(s.dataset),
        "tables": s.tables, "model_name": s.model_name,
        "connection": {"id": conn.id, "name": conn.name, "bq_connection_name": conn.bq_connection_name, "folder_layout": conn.folder_layout} if conn else None,
        "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
        "package": package_card(latest) if latest else None,
    }
    if full:
        out["messages"] = [_message_out(m) for m in s.messages]
    return out


def _cleanup_old(db: Session, current: CurrentUser) -> None:
    keep = int(current.user.preferences.get("keep_days", 90) or 0)
    if keep <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep)
    for s in db.scalars(select(ChatSession).where(ChatSession.user_id == current.id)):
        updated = s.updated_at if s.updated_at.tzinfo else s.updated_at.replace(tzinfo=timezone.utc)
        if updated < cutoff:
            db.delete(s)
    db.commit()


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    _cleanup_old(db, current)
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == current.id).order_by(ChatSession.updated_at.desc()))
    return [_session_out(s) for s in rows]


class SessionIn(BaseModel):
    project: str
    dataset: str
    dataset_label: str = ""
    tables: list[str] = Field(min_length=1, max_length=50)
    looker_connection_id: int | None = None
    model_name: str = ""
    title: str = ""


@router.post("/sessions")
def create_session(body: SessionIn, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    conn = None
    if body.looker_connection_id:
        conn = db.get(LookerConnection, body.looker_connection_id)
        if not conn or (conn.owner_id not in (None, current.id)):
            raise HTTPException(400, "That Looker connection wasn't found.")
    metas = get_metadata_provider().get_tables(body.project, body.dataset, body.tables)
    s = ChatSession(
        user_id=current.id, title=(body.title.strip() or f"New session on {body.dataset_label or humanize(body.dataset)}")[:200],
        gcp_project=body.project, dataset=body.dataset, dataset_label=body.dataset_label, tables=body.tables,
        looker_connection_id=conn.id if conn else None, model_name=snake(body.model_name or body.dataset),
    )
    s.snapshots = [TableSnapshot(table_name=m.name, meta=m.model_dump()) for m in metas]
    db.add(s)
    db.commit()
    db.refresh(s)
    return _session_out(s, full=True)


@router.get("/sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    return _session_out(_session(db, current, session_id), full=True)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    db.delete(_session(db, current, session_id))
    db.commit()
    return {"ok": True}


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/sessions/{session_id}/messages")
def post_message(session_id: int, body: MessageIn, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    s = _session(db, current, session_id)
    new = chat.handle_message(db, s, current.user, body.text)
    db.refresh(s)
    return {"messages": [_message_out(m) for m in new], "session": _session_out(s)}


class BuildIn(BaseModel):
    plan_message_id: int


@router.post("/sessions/{session_id}/build")
def build(session_id: int, body: BuildIn, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    s = _session(db, current, session_id)
    msg = db.get(Message, body.plan_message_id)
    if not msg or msg.session_id != s.id or msg.kind != "plan":
        raise HTTPException(404, "That plan wasn't found.")
    if msg.payload.get("status") == "superseded":
        raise HTTPException(400, "That plan was replaced by a newer one. Build the latest plan instead.")
    out = chat.build(db, s, msg)
    db.commit()
    db.refresh(s)
    return {"messages": [_message_out(out)], "session": _session_out(s), "plan_message": _message_out(msg)}


@router.get("/packages/{package_id}")
def get_package(package_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    p = _package(db, current, package_id)
    s = db.get(ChatSession, p.session_id)
    return {
        "id": p.id, "session_id": p.session_id, "version": p.version, "created_at": p.created_at.isoformat(),
        "summary": p.summary, "checks": p.checks, "plan": p.plan,
        "files": [{"path": f.path, "kind": f.kind, "content": f.content} for f in p.files],
        "versions": [{"id": v.id, "version": v.version, "created_at": v.created_at.isoformat()} for v in s.packages],
        "session_title": s.title,
    }


@router.get("/packages/{package_id}/diff")
def diff(package_id: int, against: int, path: str, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    new, old = _package(db, current, package_id), _package(db, current, against)
    if new.session_id != old.session_id:
        raise HTTPException(400, "Versions must come from the same session.")
    a = next((f.content for f in old.files if f.path == path), "")
    b = next((f.content for f in new.files if f.path == path), "")
    lines = difflib.unified_diff(a.splitlines(), b.splitlines(), f"version {old.version}", f"version {new.version}", lineterm="")
    return {"diff": "\n".join(lines) or "No changes in this file."}


def _zip_response(p: Package, title: str) -> Response:
    data = build_zip([(f.path, f.content) for f in p.files])
    name = f"{snake(title)[:60]}_v{p.version}.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/packages/{package_id}/download")
def download_package(package_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    p = _package(db, current, package_id)
    return _zip_response(p, db.get(ChatSession, p.session_id).title)


@router.get("/sessions/{session_id}/download")
def download_latest(session_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    s = _session(db, current, session_id)
    if not s.packages:
        raise HTTPException(404, "Nothing has been built in this session yet.")
    return _zip_response(s.packages[-1], s.title)
