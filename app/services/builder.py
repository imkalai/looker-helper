"""Build a versioned package (LookML files + plain-language results) from a plan."""

from app.config import get_settings
from app.models import ChatSession, Package, PackageFile
from app.schemas.meta import TableMeta
from app.schemas.plan import Plan
from app.services.lookml.generator import GenContext, generate
from app.services.lookml.report import build_checks, estimate_cost, included_extras, lineage, metric_text
from app.services.lookml.validate import validate_files
from app.services.metadata import get_metadata_provider
from app.services.text import humanize, snake


def session_tables(session: ChatSession) -> list[TableMeta]:
    return [TableMeta(**s.meta) for s in session.snapshots]


def connection_name(session: ChatSession) -> str:
    if session.connection:
        return session.connection.bq_connection_name
    return f"bq_{snake(session.gcp_project)}"


def thumb_type(plan: Plan) -> str:
    types = {t.type for t in plan.tiles}
    if "line" in types:
        return "line"
    if "bar" in types or "pie" in types:
        return "bars"
    return "table"


def plan_view(plan: Plan, tables: list[TableMeta]) -> dict:
    """Plan details the chat shows on a plan card, in plain language."""
    settings = get_settings()
    return {
        "plan": plan.model_dump(),
        "metrics": [{"label": m.label, "text": metric_text(m, plan)} for m in plan.metrics],
        "breakdowns": [d.label or humanize(d.column) for d in plan.dimensions]
        + ([f"{'Day' if plan.time.grain == 'date' else plan.time.grain.capitalize()} ({humanize(plan.time.column).lower()})"] if plan.time else []),
        "tiles": [t.title for t in plan.tiles],
        "included": included_extras(plan, tables, settings),
        "notes": plan.notes,
    }


def build_package(db, session: ChatSession, plan: Plan) -> Package:
    settings = get_settings()
    tables = session_tables(session)
    by = {t.name: t for t in tables}
    layout = session.connection.folder_layout if session.connection else "standard"
    gen = generate(GenContext(plan=plan, tables=tables, model_name=session.model_name,
                              connection_name=connection_name(session), folder_layout=layout, settings=settings))
    errors = validate_files(gen.files)
    cost = estimate_cost(plan, gen, by, get_metadata_provider(), settings)
    checks = build_checks(plan, gen, by, errors, cost, settings)
    label_for = {m.name: m.label for m in plan.metrics}
    dim_labels = {f"{d.table}.{d.column}": d.label or humanize(d.column) for d in plan.dimensions}
    summary = {
        "title": plan.title,
        "dashboards": 1 if gen.dashboard_name else 0,
        "metrics": len(plan.metrics),
        "files": len(gen.files),
        "thumb": thumb_type(plan),
        "cost": cost,
        "lineage": lineage(plan),
        "metric_details": [{"label": m.label, "text": metric_text(m, plan), "format": m.format, "agg": m.agg} for m in plan.metrics],
        "breakdowns": [{"label": d.label or humanize(d.column), "source": f"From the {humanize(d.table).lower()} table"} for d in plan.dimensions],
        "time": plan.time.model_dump() if plan.time else None,
        "tiles": [
            {"type": t.type, "title": t.title, "metrics": [label_for.get(m, m) for m in t.metrics],
             "dimension": ("time" if t.dimension == "time" else dim_labels.get(t.dimension or "", t.dimension)), "limit": t.limit}
            for t in plan.tiles
        ],
        "status": "error" if errors else ("review" if any(c["status"] in ("warn", "error") for c in checks) else "ready"),
        "errors": errors,
    }
    version = (max((p.version for p in session.packages), default=0)) + 1
    pkg = Package(session_id=session.id, version=version, plan=plan.model_dump(), summary=summary, checks=checks)
    pkg.files = [PackageFile(path=f.path, kind=f.kind, content=f.content) for f in gen.files]
    session.packages.append(pkg)
    session.status = summary["status"] if summary["status"] != "error" else "review"
    if session.title.startswith("New session"):
        session.title = plan.title
    db.add(pkg)
    db.flush()
    return pkg


def package_card(pkg: Package) -> dict:
    s = pkg.summary
    return {
        "package_id": pkg.id, "version": pkg.version, "title": s.get("title"), "dashboards": s.get("dashboards", 0),
        "metrics": s.get("metrics", 0), "files": s.get("files", 0), "thumb": s.get("thumb"), "status": s.get("status"),
        "cost_text": s.get("cost", {}).get("bytes_text"), "tips": sum(1 for c in pkg.checks if c["status"] == "tip"),
        "warnings": sum(1 for c in pkg.checks if c["status"] in ("warn", "error")),
    }
