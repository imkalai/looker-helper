"""Stand-in for Cortex. Answers the same prompts as the real client, using keyword rules."""

import json
import re

from app.schemas.meta import TableMeta
from app.schemas.plan import Plan
from app.services.cortex import mock_logic
from app.services.cortex.base import CortexClient, CortexError

_TASK = re.compile(r"TASK:\s*(\w+)")
_CONTEXT = re.compile(r"<context>(.*?)</context>", re.S)


class MockCortex(CortexClient):
    name = "mock"

    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 4000) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        task = (_TASK.search(system) or [None, ""])[1]
        if task == "ping":
            return "OK"
        match = _CONTEXT.search(user)
        if not match:
            raise CortexError("The request was missing its context.")
        ctx = json.loads(match.group(1))
        tables = [TableMeta(**t) for t in ctx.get("tables", [])]
        rules = mock_logic.Rules(tables) if tables else None
        message = ctx.get("message", "")
        plan = Plan(**ctx["current_plan"]) if ctx.get("current_plan") else None

        if task == "suggest_tables":
            return json.dumps({"tables": rules.suggest_tables(message)})
        if task == "classify":
            return json.dumps({"intent": mock_logic.classify(message, plan is not None)})
        if task == "plan":
            return rules.plan_from_request(message).model_dump_json()
        if task == "revise":
            return rules.revise(plan, message).model_dump_json()
        if task == "explain":
            return json.dumps({"answer": mock_logic.explain(message, tables, plan)})
        raise CortexError(f"Unknown task '{task}'.")

    def health(self) -> tuple[bool, str]:
        return True, "Using the built-in stand-in for Cortex (development mode)"
