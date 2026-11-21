"""One chat turn: understand the message, then plan, change, build or answer."""

import logging

from sqlalchemy.orm import Session

from app.models import ChatSession, Message, User, now
from app.schemas.plan import Plan
from app.services import planner
from app.services.builder import build_package, package_card, plan_view, session_tables
from app.services.cortex import CortexError, get_cortex
from app.services.planner import PlanError

log = logging.getLogger(__name__)


def _add(db: Session, session: ChatSession, role: str, kind: str, content: str, payload: dict | None = None) -> Message:
    msg = Message(session_id=session.id, role=role, kind=kind, content=content, payload=payload or {})
    session.messages.append(msg)
    session.updated_at = now()
    db.flush()
    return msg


def latest_plan(session: ChatSession) -> Message | None:
    for m in reversed(session.messages):
        if m.kind == "plan" and m.payload.get("status") in ("pending", "built"):
            return m
    return None


def _plan_message(db, session, plan: Plan, content: str, status: str = "pending") -> Message:
    for m in session.messages:
        if m.kind == "plan" and m.payload.get("status") == "pending":
            m.payload = {**m.payload, "status": "superseded"}
    payload = {**plan_view(plan, session_tables(session)), "status": status}
    return _add(db, session, "assistant", "plan", content, payload)


def build(db: Session, session: ChatSession, plan_msg: Message, intro: str = "Done. Your dashboard and files are ready.") -> Message:
    plan = Plan(**plan_msg.payload["plan"])
    pkg = build_package(db, session, plan)
    plan_msg.payload = {**plan_msg.payload, "status": "built", "package_id": pkg.id}
    return _add(db, session, "assistant", "package", intro, package_card(pkg))


def handle_message(db: Session, session: ChatSession, user: User, text: str) -> list[Message]:
    text = text.strip()
    new: list[Message] = [_add(db, session, "user", "text", text)]
    tables = session_tables(session)
    cortex = get_cortex(db)
    plan_msg = latest_plan(session)
    plan = Plan(**plan_msg.payload["plan"]) if plan_msg else None
    confirm = user.preferences.get("confirm_plan", True)
    try:
        intent = planner.classify(cortex, text, plan, tables)
        if intent == "question":
            answer = planner.explain(cortex, text, plan, tables)
            new.append(_add(db, session, "assistant", "text", answer))
        elif intent == "build":
            if plan_msg.payload.get("status") == "built":
                new.append(_add(db, session, "assistant", "text",
                                "That's already built. Open the package to see it, or tell me what you'd like to change."))
            else:
                new.append(build(db, session, plan_msg))
        elif intent == "new_plan":
            plan = planner.make_plan(cortex, text, tables)
            if session.title.startswith("New session"):
                session.title = plan.title
            msg = _plan_message(db, session, plan, "Here's what I'll build. Does this look right?")
            new.append(msg)
            if not confirm:
                new.append(build(db, session, msg))
        else:  # change | change_and_build
            revised = planner.revise_plan(cortex, plan, text, tables)
            if intent == "change_and_build" or not confirm:
                msg = _plan_message(db, session, revised, "Here's the updated plan.")
                new.append(msg)
                new.append(build(db, session, msg, "Done. I've made that change and your files are ready."))
            else:
                new.append(_plan_message(db, session, revised, "Here's the updated plan. Does this look right?"))
    except (PlanError, CortexError) as exc:
        new.append(_add(db, session, "assistant", "error", str(exc)))
    except Exception:  # keep the chat usable whatever happens
        log.exception("Chat turn failed")
        new.append(_add(db, session, "assistant", "error", "Something went wrong on our side. Try again, or rephrase your request."))
    db.commit()
    return new
