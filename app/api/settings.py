"""The signed-in user's preferences, and the admin-only Cortex configuration."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.config import get_settings
from app.db import get_db
from app.models import AppSetting
from app.security import encrypt
from app.services.cortex import CORTEX_SETTING_KEY, cortex_config, get_cortex

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/me")
def me(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    settings = get_settings()
    u = current.user
    initials = "".join(p[0] for p in u.display_name.split()[:2]).upper() or u.email[:2].upper()
    return {
        "email": u.email, "name": u.display_name, "initials": initials, "is_admin": current.is_admin,
        "preferences": {"theme": "system", "confirm_plan": True, "show_code_first": False, "keep_days": 90, **u.preferences},
        "metadata_mode": settings.metadata_mode, "cortex_mode": settings.cortex_mode,
    }


class PreferencesIn(BaseModel):
    theme: Literal["light", "dark", "system"] | None = None
    confirm_plan: bool | None = None
    show_code_first: bool | None = None
    keep_days: int | None = None
    default_project: str | None = None
    default_dataset: str | None = None
    default_connection_id: int | None = None


@router.put("/me/preferences")
def update_preferences(body: PreferencesIn, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    changes = body.model_dump(exclude_unset=True)
    current.user.preferences = {**current.user.preferences, **changes}
    db.add(current.user)
    db.commit()
    return {"preferences": current.user.preferences}


@router.get("/cortex")
def get_cortex_settings(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    cfg = cortex_config(db)
    ok, message = get_cortex(db).health()
    return {
        "mode": get_settings().cortex_mode, "ok": ok, "message": message, "can_edit": current.is_admin,
        "endpoint": cfg.get("endpoint", ""), "auth_method": cfg.get("auth_method", "api_key"), "model": cfg.get("model", "default"),
        "timeout": cfg.get("timeout", 60), "retries": cfg.get("retries", 2), "headers": cfg.get("headers_text", ""),
        "proxy": cfg.get("proxy", ""), "ca_bundle": cfg.get("ca_bundle", ""), "has_key": bool(cfg.get("api_key")),
    }


class CortexIn(BaseModel):
    endpoint: str = ""
    auth_method: Literal["api_key", "sso_token", "service_account"] = "api_key"
    api_key: str | None = None  # None = keep the stored key
    model: str = "default"
    timeout: int = 60
    retries: int = 2
    headers: str = ""
    proxy: str = ""
    ca_bundle: str = ""


def _parse_headers(text: str) -> dict:
    out = {}
    for line in text.replace(";", "\n").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


@router.put("/cortex")
def update_cortex_settings(body: CortexIn, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    row = db.get(AppSetting, CORTEX_SETTING_KEY) or AppSetting(key=CORTEX_SETTING_KEY, value={})
    value = dict(row.value)
    value.update({
        "endpoint": body.endpoint.strip(), "auth_method": body.auth_method, "model": body.model.strip() or "default",
        "timeout": max(5, min(body.timeout, 600)), "retries": max(0, min(body.retries, 5)),
        "headers_text": body.headers, "headers": _parse_headers(body.headers), "proxy": body.proxy.strip(), "ca_bundle": body.ca_bundle.strip(),
    })
    if body.api_key is not None:
        value["api_key_enc"] = encrypt(body.api_key)
    row.value = value
    db.merge(row)
    db.commit()
    return {"ok": True}


@router.post("/cortex/test")
def test_cortex(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)):
    ok, message = get_cortex(db).health()
    return {"ok": ok, "message": message}
