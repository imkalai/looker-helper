"""Plain-language results for business users: checks, where numbers come from, cost, and plan descriptions."""

from app.config import Settings
from app.schemas.meta import TableMeta
from app.schemas.plan import Filter, Metric, Plan
from app.services.lookml.generator import GeneratedPackage
from app.services.metadata.base import MetadataProvider
from app.services.text import format_bytes, humanize

_OP_TEXT = {
    "equals": "only {v}", "not_equals": "excluding {v}", "in": "only {v}", "not_in": "excluding {v}",
    "greater_than": "where {c} is above {v}", "less_than": "where {c} is below {v}",
}


def filter_text(f: Filter, tables: dict[str, TableMeta] | None = None) -> str:
    if f.label:
        return f.label
    vals = " or ".join(v for v in f.values)
    noun = humanize(f.table).lower()
    if f.operator in ("equals", "not_equals", "in", "not_in"):
        return _OP_TEXT[f.operator].format(v=f"{vals} {noun}", c=f.column)
    return _OP_TEXT[f.operator].format(v=vals, c=f.column.replace("_", " "))


def metric_text(m: Metric, plan: Plan) -> str:
    """Plain description of how a metric is calculated."""
    col = (m.column or "").replace("_", " ")
    table = humanize(m.table).lower()
    if m.agg == "sum":
        base = f"Sum of {col} from {table}"
    elif m.agg == "count":
        base = f"Each row in {table} counted once"
    elif m.agg == "count_distinct":
        base = f"Number of different {col} values in {table}"
    elif m.agg == "average":
        base = f"Average {col} in {table}"
    elif m.agg == "min":
        base = f"Smallest {col} in {table}"
    elif m.agg == "max":
        base = f"Largest {col} in {table}"
    else:
        num, den = plan.metric(m.numerator or ""), plan.metric(m.denominator or "")
        base = f"{num.label if num else m.numerator} ÷ {(den.label if den else m.denominator or '').lower()}"
    if plan.filters and m.agg != "ratio":
        base += ", " + ", ".join(filter_text(f) for f in plan.filters)
    return base


def included_extras(plan: Plan, tables: list[TableMeta], settings: Settings) -> list[str]:
    """The automatic extras worth mentioning in one sentence on the plan card."""
    out = []
    if plan.time and settings.enable_period_over_period:
        out.append("comparison with last year")
    if plan.tiles:
        out.append("click any chart to filter the rest")
    by_name = {t.name: t for t in tables}
    base = by_name.get(plan.base_table)
    if base and base.partition_field and (base.size_bytes or 0) >= settings.large_table_bytes:
        out.append(f"the last {settings.default_months} months shown by default to keep it quick")
    if any(c.is_pii for t in tables for c in t.columns):
        out.append("personal data kept hidden")
    return out


def estimate_cost(plan: Plan, pkg: GeneratedPackage, tables: dict[str, TableMeta], provider: MetadataProvider, settings: Settings) -> dict:
    """Bytes read per dashboard refresh: BigQuery dry run in live mode, a size-based estimate otherwise."""
    used: dict[str, set[str]] = {}

    def use(table: str, col: str | None):
        if col:
            used.setdefault(table, set()).add(col)

    for m in plan.metrics:
        use(m.table, m.column)
    for d in plan.dimensions:
        use(d.table, d.column)
    if plan.time:
        use(plan.time.table, plan.time.column)
    for f in plan.filters:
        use(f.table, f.column)
    for j in pkg.joins:
        if j.to_table in used or j.from_table in used:
            use(j.from_table, j.from_col)
            use(j.to_table, j.to_col)
    used.setdefault(plan.base_table, set())

    months = settings.default_months
    filtered = pkg.extras.always_filter is not None
    total, unfiltered, exact = 0, 0, True
    for name, cols in used.items():
        t = tables[name]
        cols = cols or {t.columns[0].name}
        date_cond = ""
        if filtered and name == plan.base_table and t.partition_field:
            pcol = t.column(t.partition_field)
            cast = {"DATE": "", "DATETIME": "DATETIME", "TIMESTAMP": "TIMESTAMP"}.get(pcol.type.upper() if pcol else "", "")
            bound = f"DATE_SUB(CURRENT_DATE(), INTERVAL {months} MONTH)"
            date_cond = f" WHERE {t.partition_field} >= {cast}({bound})" if cast else f" WHERE {t.partition_field} >= {bound}"
        sql = f"SELECT {', '.join(sorted(cols))} FROM `{t.full_name}`{date_cond}"
        b = provider.dry_run_bytes(t.project, sql)
        share = len(cols) / max(len(t.columns), 1)
        full = int((t.size_bytes or 0) * share)
        if b is None:
            exact = False
            b = full
            if date_cond and t.history_months:
                b = int(full * min(1.0, months / t.history_months))
        total += b
        unfiltered += full
    gbp = total / 1024**4 * settings.price_per_tib_gbp
    return {
        "bytes": total,
        "bytes_text": format_bytes(total),
        "unfiltered_bytes_text": format_bytes(unfiltered) if filtered else None,
        "cost_gbp": round(gbp, 4),
        "cost_text": "under £0.01" if gbp < 0.01 else f"about £{gbp:.2f}",
        "method": "BigQuery dry run" if exact else "estimate from table sizes",
    }


def build_checks(plan: Plan, pkg: GeneratedPackage, tables: dict[str, TableMeta], errors: list[str], cost: dict, settings: Settings) -> list[dict]:
    checks: list[dict] = []
    if errors:
        checks.append({"status": "error", "title": "Some files need fixing", "detail": "; ".join(errors[:3])})
    else:
        checks.append({"status": "ok", "title": "Files are valid", "detail": "Every file was checked for correct LookML and every field it uses exists."})

    ex = pkg.extras
    if ex.fanout_joins:
        detail = "Combining " + ", ".join(ex.fanout_joins) + " could count some rows more than once. Each table has a unique ID, so Looker keeps totals correct."
    else:
        detail = "Each table has a unique ID, so totals stay correct when tables are combined."
    if ex.surrogate_keys:
        detail += f" A row ID was added for {', '.join(humanize(t).lower() for t in ex.surrogate_keys)}."
    checks.append({"status": "ok", "title": "Totals are correct", "detail": detail})

    if ex.tests:
        checks.append({"status": "ok", "title": "Data checks included", "detail": "Looker will check that " + "; ".join(ex.tests[:4]).lower() + "."})
    if ex.pii_columns:
        names = ", ".join(c.split(".")[1].replace("_", " ") for c in ex.pii_columns[:4])
        checks.append({"status": "ok", "title": "Personal data is hidden", "detail": f"{names[:1].upper() + names[1:]} can only be seen by people allowed to see personal data."})

    speed = f"Each refresh reads {cost['bytes_text']} ({cost['cost_text']})."
    if ex.always_filter:
        speed = f"Shows the last {settings.default_months} months by default. " + speed
        if cost.get("unfiltered_bytes_text"):
            speed += f" Without that it would read {cost['unfiltered_bytes_text']}."
    speed += " Results are reused until new data arrives."
    big = cost["bytes"] > settings.cost_warn_gb * 1024**3
    checks.append({"status": "warn" if big else "ok", "title": "Costly to refresh" if big else "Quick and low cost", "detail": speed})

    for m in plan.metrics:
        if m.column and m.agg in ("sum", "average"):
            col = tables[m.table].column(m.column)
            if col and not col.description:
                checks.append({"status": "tip", "title": "One tip", "detail": f"The {col.name} column has no description in BigQuery. Adding one helps your team understand “{m.label}”."})
                break
    return checks


def lineage(plan: Plan) -> list[dict]:
    rows = [{"name": m.label, "source": metric_text(m, plan)} for m in plan.metrics]
    for d in plan.dimensions:
        rows.append({"name": d.label or humanize(d.column), "source": f"From the {humanize(d.table)} table"})
    if plan.time:
        rows.append({"name": f"Date ({plan.time.grain})", "source": f"{humanize(plan.time.column)} in the {humanize(plan.time.table).lower()} table"})
    return rows
