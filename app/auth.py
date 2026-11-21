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


def _email_from_request(request: Request) -> str | None:
    settings = get_settings()
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
