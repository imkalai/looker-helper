import logging
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User


@dataclass
class CurrentUser:
    user: User
    is_admin: bool

    @property
    def id(self) -> int:
        return self.user.id


log = logging.getLogger(__name__)
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"
_iap_cache: dict[str, tuple[str, float]] = {}  # token -> (email, expires_at); IAP reuses a token for several minutes


def _verify_iap(token: str, audience: str) -> str | None:
    hit = _iap_cache.get(token)
    if hit and hit[1] > time.time():
        return hit[0]
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_token(token, g_requests.Request(), audience=audience, certs_url=IAP_CERTS_URL)
    except Exception:  # bad signature, wrong audience, expired, ...
        log.warning("Rejected an IAP token that didn't verify")
        return None
    email = str(claims.get("email", "")).lower()
    if len(_iap_cache) > 5000:
        _iap_cache.clear()
    _iap_cache[token] = (email, float(claims.get("exp", time.time())))
    return email or None


def _email_from_request(request: Request) -> str | None:
    settings = get_settings()
    if settings.iap_audience:
        token = request.headers.get("x-goog-iap-jwt-assertion", "")
        return _verify_iap(token, settings.iap_audience) if token else None
    raw = request.headers.get(settings.auth_header, "")
    # IAP sends "accounts.google.com:user@example.com"
    email = raw.split(":", 1)[-1].strip().lower() if raw else ""
    if not email and settings.dev_user_email:
        email = settings.dev_user_email.strip().lower()
    return email or None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    email = _email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="You're not signed in. Open the app through the company sign-in page.")
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        name = email.split("@")[0].replace(".", " ").title()
        user = User(email=email, display_name=name, preferences={})
        db.add(user)
        db.commit()
        db.refresh(user)
    settings = get_settings()
    admins = settings.admin_list
    is_admin = email in admins or (not admins and email == (settings.dev_user_email or "").lower())
    return CurrentUser(user=user, is_admin=is_admin)


def require_admin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.is_admin:
        raise HTTPException(status_code=403, detail="Only administrators can change this setting.")
    return current
