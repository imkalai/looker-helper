"""Talks to Cortex and makes sure every answer only uses the session's tables and columns."""

import json
import logging
import re

from pydantic import ValidationError

from app.schemas.meta import TableInfo, TableMeta
from app.schemas.plan import Plan, Tile
from app.services.cortex.base import CortexClient, CortexError
from app.services.cortex.prompts import build_messages, tables_context
from app.services.lookml.relations import reachable
from app.services.text import humanize, snake

log = logging.getLogger(__name__)
INTENTS = {"new_plan", "change", "build", "change_and_build", "question"}


class PlanError(Exception):
    """The plan couldn't be made. The message can be shown to the user."""


def _json(reply: str) -> dict:
    text = reply.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start : end + 1])


def _ask(cortex: CortexClient, task: str, context: dict, user_text: str, errors: list[str] | None = None) -> dict:
    reply = cortex.chat(build_messages(task, context, user_text, errors))
    return _json(reply)


def validate_plan(plan: Plan, tables: list[TableMeta]) -> tuple[Plan, list[str]]:
    """Return a cleaned-up plan and the problems that need Cortex to try again."""
    errors: list[str] = []
    by = {t.name: t for t in tables}
    if plan.base_table not in by:
        return plan, [f"base_table '{plan.base_table}' is not one of the session's tables: {', '.join(by)}"]
    depth = reachable(plan.base_table, tables)
    notes = list(plan.notes)

    seen = set()
    for m in plan.metrics:
        m.name = snake(m.name)
        if m.name in seen:
            errors.append(f"metric name '{m.name}' is used twice")
        seen.add(m.name)
        if m.table not in by:
            errors.append(f"metric '{m.name}' uses unknown table '{m.table}'")
            continue
        if m.table not in depth:
            errors.append(f"metric '{m.name}' uses table '{m.table}', which can't be joined to '{plan.base_table}'")
        if m.agg == "ratio":
            for ref in (m.numerator, m.denominator):
                if not ref or snake(ref) not in {snake(x.name) for x in plan.metrics}:
                    errors.append(f"ratio metric '{m.name}' refers to unknown metric '{ref}'")
            m.numerator, m.denominator = snake(m.numerator or ""), snake(m.denominator or "")
        elif m.agg != "count":
            col = by[m.table].column(m.column or "")
            if not col:
                errors.append(f"metric '{m.name}' uses unknown column '{m.table}.{m.column}'")
            elif m.agg in ("sum", "average") and not col.is_numeric:
                errors.append(f"metric '{m.name}' can't {m.agg} the non-numeric column '{m.table}.{col.name}'")
            else:
                m.column = col.name
    if not plan.metrics:
        errors.append("the plan has no metrics")

    dims = []
    for d in plan.dimensions:
        t = by.get(d.table)
        col = t.column(d.column) if t else None
        if not col:
            errors.append(f"dimension uses unknown column '{d.table}.{d.column}'")
        elif d.table not in depth:
            errors.append(f"dimension '{d.table}.{d.column}' can't be joined to '{plan.base_table}'")
        elif col.is_pii:
            notes.append(f"{humanize(col.name)} was left out because it's personal data.")
        else:
            d.column = col.name
            d.label = d.label or humanize(col.name)
            dims.append(d)
    plan.dimensions = dims

    if plan.time:
        t = by.get(plan.time.table)
        col = t.column(plan.time.column) if t else None
        if not col or not col.is_time:
            errors.append(f"time column '{plan.time.table}.{plan.time.column}' doesn't exist or isn't a date/time column")
        elif plan.time.table not in depth:
            errors.append(f"time column table '{plan.time.table}' can't be joined to '{plan.base_table}'")
        else:
            plan.time.column = col.name

    for f in plan.filters:
        t = by.get(f.table)
        col = t.column(f.column) if t else None
        if not col:
            errors.append(f"filter uses unknown column '{f.table}.{f.column}'")
        elif f.table not in depth:
            errors.append(f"filter column '{f.table}.{f.column}' can't be joined to '{plan.base_table}'")
        else:
            f.column = col.name
        if not f.values:
            errors.append(f"filter on '{f.table}.{f.column}' has no values")

    names = {m.name for m in plan.metrics}
    tiles: list[Tile] = []
    for tile in plan.tiles:
        tile.metrics = [snake(x) for x in tile.metrics if snake(x) in names]
        if not tile.metrics:
            continue
        if tile.dimension == "time" and not plan.time:
            continue
        if tile.dimension and tile.dimension != "time":
            table, _, col = tile.dimension.partition(".")
            t = by.get(table)
            c = t.column(col) if t else None
            if not c or table not in depth or c.is_pii:
                continue
            tile.dimension = f"{table}.{c.name}"
        tiles.append(tile)
    if not tiles and plan.metrics:
        tiles = [Tile(type="kpi", title=m.label, metrics=[m.name]) for m in plan.metrics[:4]]
    plan.tiles = tiles
    plan.notes = notes
    fallback_title = plan.metrics[0].label if plan.metrics else "Dashboard"
    plan.title = (plan.title or fallback_title).strip()[:120]
    return plan, errors


def _plan_task(cortex: CortexClient, task: str, context: dict, text: str, tables: list[TableMeta]) -> Plan:
    errors: list[str] | None = None
    for attempt in range(2):
        try:
            data = _ask(cortex, task, context, text, errors)
            plan = Plan(**data)
        except (ValueError, ValidationError) as exc:
            errors = [f"the reply wasn't a valid plan: {str(exc)[:300]}"]
            continue
        plan, errors = validate_plan(plan, tables)
        if not errors:
            return plan
        log.info("Plan attempt %s had problems: %s", attempt + 1, errors)
    raise PlanError("I couldn't turn that into a plan using your tables. Try naming the numbers you want and how to split them, "
                    "for example “revenue by region for each month”.")


def make_plan(cortex: CortexClient, request: str, tables: list[TableMeta]) -> Plan:
    ctx = {"tables": tables_context(tables), "message": request}
    return _plan_task(cortex, "plan", ctx, f"Request: {request}", tables)


def revise_plan(cortex: CortexClient, plan: Plan, change: str, tables: list[TableMeta]) -> Plan:
    ctx = {"tables": tables_context(tables), "message": change, "current_plan": plan.model_dump()}
    return _plan_task(cortex, "revise", ctx, f"Change: {change}", tables)


def classify(cortex: CortexClient, message: str, plan: Plan | None, tables: list[TableMeta]) -> str:
    ctx = {"message": message, "current_plan": plan.model_dump() if plan else None, "tables": tables_context(tables)}
    try:
        intent = _ask(cortex, "classify", ctx, f"Message: {message}").get("intent", "")
    except (ValueError, CortexError):
        intent = ""
    if intent not in INTENTS:
        intent = "change" if plan else "new_plan"
    if not plan and intent in ("change", "build", "change_and_build"):
        intent = "new_plan"
    return intent


def explain(cortex: CortexClient, question: str, plan: Plan | None, tables: list[TableMeta]) -> str:
    ctx = {"message": question, "current_plan": plan.model_dump() if plan else None, "tables": tables_context(tables)}
    try:
        return str(_ask(cortex, "explain", ctx, f"Question: {question}").get("answer", "")).strip() or "I'm not sure. Try asking about a table or a number in the plan."
    except ValueError:
        return "I couldn't answer that just now. Try asking in a different way."


def suggest_tables(cortex: CortexClient, request: str, tables: list[TableMeta], infos: list[TableInfo]) -> list[str]:
    if not request.strip():
        return []
    ctx = {"message": request, "tables": tables_context(tables)}
    try:
        names = _ask(cortex, "suggest_tables", ctx, f"Request: {request}").get("tables", [])
    except (ValueError, CortexError):
        return []
    valid = {i.name for i in infos}
    return [n for n in names if n in valid]
