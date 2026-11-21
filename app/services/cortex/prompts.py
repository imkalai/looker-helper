"""Prompts sent to Cortex. Each starts with "TASK: <name>" and carries its data in a <context> JSON block,
so the mock client can answer the same messages the real one receives."""

import json

from app.schemas.meta import TableMeta
from app.schemas.plan import Plan

_PLAN_SCHEMA = json.dumps(Plan.model_json_schema(), separators=(",", ":"))

_COMMON = (
    "You help business users build Looker dashboards from BigQuery tables. "
    "Use ONLY the tables and columns listed in the context. Never invent columns. "
    "Reply with a single JSON object and nothing else."
)

SYSTEM = {
    "suggest_tables": (
        "TASK: suggest_tables\n" + _COMMON + "\n"
        'Pick the tables needed to answer the request, including any table needed to join them. '
        'Reply as {"tables": ["name", ...]}.'
    ),
    "classify": (
        "TASK: classify\n" + _COMMON + "\n"
        "Decide what the user's latest message wants, given whether a plan already exists.\n"
        'Reply as {"intent": "new_plan" | "change" | "build" | "change_and_build" | "question"}.\n'
        "build = approves the current plan; change = modifies it; change_and_build = approves with a change "
        '(e.g. "yes, but leave out cancelled orders"); question = asks about the data or the plan.'
    ),
    "plan": (
        "TASK: plan\n" + _COMMON + "\n"
        "Turn the request into a dashboard plan. Use snake_case metric names and plain-English labels. "
        "Use agg=ratio with numerator/denominator metric names for things like average order value. "
        "Put exclusions like 'leave out cancelled orders' in filters. "
        "Tiles: a kpi per main metric, a line tile over time if a time grain is asked for, a bar tile per breakdown. "
        "Tile dimension is \"time\" or \"table.column\".\n"
        f"JSON schema: {_PLAN_SCHEMA}"
    ),
    "revise": (
        "TASK: revise\n" + _COMMON + "\n"
        "Apply the user's change to the current plan and return the complete updated plan. Keep everything else the same.\n"
        f"JSON schema: {_PLAN_SCHEMA}"
    ),
    "explain": (
        "TASK: explain\n" + _COMMON + "\n"
        "Answer the user's question in plain English for a business user: short sentences, no SQL or LookML terms. "
        'Reply as {"answer": "..."}.'
    ),
}


def tables_context(tables: list[TableMeta]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "row_count": t.row_count,
            "size_bytes": t.size_bytes,
            "partition_field": t.partition_field,
            "history_months": t.history_months,
            "project": t.project,
            "dataset": t.dataset,
            "columns": [
                {"name": c.name, "type": c.type, "mode": c.mode, "description": c.description, "is_pii": c.is_pii,
                 "sample_values": c.sample_values[:8]}
                for c in t.columns if c.is_supported
            ],
        }
        for t in tables
    ]


def build_messages(task: str, context: dict, user_text: str, errors: list[str] | None = None) -> list[dict]:
    user = f"<context>{json.dumps(context, default=str)}</context>\n\n{user_text}"
    if errors:
        user += "\n\nYour previous answer had these problems, fix them:\n- " + "\n- ".join(errors)
    return [{"role": "system", "content": SYSTEM[task]}, {"role": "user", "content": user}]
