"""Turn a validated Plan plus BigQuery table details into LookML files.

Good Looker practice is applied automatically (business users never choose these):
- every view has a primary key, so totals stay correct across joins (symmetric aggregates)
- large partitioned tables get an always-on date filter, so dashboards don't scan whole tables
- results are cached until the dataset changes (datagroup)
- personal-data columns are hidden behind an access grant
- data tests check primary keys are unique and money totals aren't negative
- "vs last year" measures, cross-filtering and drill fields on the dashboard
"""

from dataclasses import dataclass, field

import yaml

from app.config import Settings, get_settings
from app.schemas.meta import ColumnMeta, TableMeta
from app.schemas.plan import Metric, Plan, Tile
from app.services.lookml.relations import JoinStep, infer_relations, join_tree, primary_key
from app.services.text import lookml_string, singular, snake

TIME_SUFFIXES = ("_timestamp", "_datetime", "_date", "_time", "_at", "_ts")
SURROGATE_PK = "row_key"


@dataclass
class GenFile:
    path: str
    kind: str  # view | model | dashboard
    content: str


@dataclass
class Extras:
    """What the generator applied automatically, reported to the user in plain language."""

    period_over_period: list[str] = field(default_factory=list)
    crossfilter: bool = False
    drill_fields: bool = False
    pii_columns: list[str] = field(default_factory=list)
    always_filter: str | None = None
    datagroup: str | None = None
    tests: list[str] = field(default_factory=list)
    surrogate_keys: list[str] = field(default_factory=list)
    fanout_joins: list[str] = field(default_factory=list)


@dataclass
class GeneratedPackage:
    files: list[GenFile]
    explore: str
    joins: list[JoinStep]
    metric_fields: dict[str, str]
    extras: Extras
    dashboard_name: str | None


@dataclass
class GenContext:
    plan: Plan
    tables: list[TableMeta]
    model_name: str
    connection_name: str
    folder_layout: str = "standard"  # standard | flat
    settings: Settings | None = None

    def __post_init__(self):
        self.settings = self.settings or get_settings()
        self.by_name = {t.name: t for t in self.tables}


def time_group_name(column: str, taken: set[str]) -> str:
    lower = column.lower()
    for suffix in TIME_SUFFIXES:
        if lower.endswith(suffix) and len(lower) > len(suffix):
            candidate = column[: -len(suffix)]
            if candidate not in taken:
                return candidate
    return column


class _Writer:
    def __init__(self):
        self.lines: list[str] = []
        self.depth = 0

    def line(self, text: str = "") -> None:
        self.lines.append(("  " * self.depth + text) if text else "")

    def open(self, text: str) -> None:
        self.line(text + " {")
        self.depth += 1

    def close(self) -> None:
        self.depth -= 1
        self.line("}")

    def text(self) -> str:
        return "\n".join(self.lines).rstrip() + "\n"


class _ViewModel:
    """Field names for one table's view."""

    def __init__(self, table: TableMeta, fk_columns: set[str]):
        self.table = table
        self.name = snake(table.name)
        self.pk = primary_key(table)
        self.fk_columns = fk_columns
        self.columns = [c for c in table.columns if c.is_supported]
        taken = {c.name for c in self.columns}
        self.time_groups: dict[str, str] = {}
        for c in self.columns:
            if c.is_time:
                group = time_group_name(c.name, taken - {c.name})
                taken.add(group)
                self.time_groups[c.name] = group
        self.measures: list[tuple[Metric, str]] = []

    def field_for(self, column: str, grain: str | None = None) -> str:
        col = self.table.column(column)
        name = col.name if col else column
        if name in self.time_groups:
            return f"{self.time_groups[name]}_{grain or 'date'}"
        return name


def _value_format(metric: Metric, settings: Settings) -> str | None:
    if metric.format == "currency":
        return settings.currency_format
    if metric.format == "percent":
        return "percent_1"
    if metric.agg in ("average", "ratio"):
        return "decimal_2"
    return None


def _sql_literal(value: str) -> str:
    try:
        float(value)
        return value
    except ValueError:
        return "'" + value.replace("'", "\\'") + "'"


class Generator:
    def __init__(self, ctx: GenContext):
        self.ctx = ctx
        self.settings = ctx.settings
        self.plan = ctx.plan
        self.extras = Extras()
        base = self.plan.base_table
        self.joins = join_tree(base, ctx.tables)
        in_explore = [base] + [j.to_table for j in self.joins]
        rels = infer_relations(ctx.tables)
        fks: dict[str, set[str]] = {}
        for r in rels:
            fks.setdefault(r.child, set()).add(r.child_col)
        self.views = {n: _ViewModel(ctx.by_name[n], fks.get(n, set())) for n in in_explore}
        self.explore = self.views[base].name
        self.metric_fields: dict[str, str] = {}
        self._place_measures()

    # ---------- placement ----------
    def _place_measures(self) -> None:
        for m in self.plan.metrics:
            table = m.table
            if m.agg == "ratio" and m.numerator:
                num = self.plan.metric(m.numerator)
                table = num.table if num else m.table
            vm = self.views[table]
            name = snake(m.name)
            if name in {c.name for c in vm.columns} or name in vm.time_groups.values() or name in ("count", SURROGATE_PK):
                name = f"{name}_measure"
            vm.measures.append((m, name))
            self.metric_fields[m.name] = f"{vm.name}.{name}"

    def _ref(self, metric_name: str, from_view: str) -> str:
        full = self.metric_fields[metric_name]
        view, fld = full.split(".", 1)
        return f"${{{fld}}}" if view == from_view else f"${{{full}}}"

    @property
    def _time_view(self) -> _ViewModel | None:
        t = self.plan.time
        return self.views.get(t.table) if t else None

    def _always_filter_field(self) -> str | None:
        base = self.ctx.by_name[self.plan.base_table]
        part = base.partition_field
        if not part or (base.size_bytes or 0) < self.settings.large_table_bytes:
            return None
        vm = self.views[base.name]
        if part not in vm.time_groups:
            return None
        return f"{vm.name}.{vm.time_groups[part]}_date"

    # ---------- views ----------
    def _dimension(self, w: _Writer, vm: _ViewModel, c: ColumnMeta) -> None:
        dims_labels = {(d.table, d.column): d.label for d in self.plan.dimensions}
        label = dims_labels.get((vm.table.name, c.name))
        if c.is_time:
            w.open(f"dimension_group: {vm.time_groups[c.name]}")
            w.line("type: time")
            if c.type.upper() == "DATE":
                w.line("timeframes: [raw, date, week, month, quarter, year]")
                w.line("convert_tz: no")
                w.line("datatype: date")
            else:
                w.line("timeframes: [raw, time, date, week, month, quarter, year]")
                if c.type.upper() == "DATETIME":
                    w.line("datatype: datetime")
        else:
            w.open(f"dimension: {c.name}")
            if c.name == vm.pk:
                w.line("primary_key: yes")
            if c.is_numeric:
                w.line("type: number")
            elif c.is_bool:
                w.line("type: yesno")
            else:
                w.line("type: string")
        if label:
            w.line(f'label: "{lookml_string(label)}"')
        if c.description:
            w.line(f'description: "{lookml_string(c.description)}"')
        if c.name in vm.fk_columns:
            w.line("hidden: yes")
        if c.is_pii:
            w.line(f"required_access_grants: [{self.settings.pii_access_grant}]")
            self.extras.pii_columns.append(f"{vm.table.name}.{c.name}")
        w.line(f"sql: ${{TABLE}}.{c.name} ;;")
        w.close()
        w.line()

    def _measure(self, w: _Writer, vm: _ViewModel, m: Metric, name: str) -> None:
        w.open(f"measure: {name}")
        w.line(f'label: "{lookml_string(m.label)}"')
        if m.description:
            w.line(f'description: "{lookml_string(m.description)}"')
        if m.agg == "count":
            w.line("type: count")
            w.line("drill_fields: [detail*]")
        elif m.agg == "ratio":
            w.line("type: number")
            w.line(f"sql: {self._ref(m.numerator, vm.name)} / NULLIF({self._ref(m.denominator, vm.name)}, 0) ;;")
        else:
            w.line(f"type: {m.agg}")
            col = vm.table.column(m.column or "")
            if col is None:
                target = f"${{TABLE}}.{m.column}"
            elif col.is_time:
                target = f"${{TABLE}}.{col.name}"
            else:
                target = f"${{{col.name}}}"
            w.line(f"sql: {target} ;;")
            if m.agg in ("sum", "count_distinct"):
                w.line("drill_fields: [detail*]")
        fmt = _value_format(m, self.settings)
        if fmt:
            w.line(f"value_format_name: {fmt}")
        w.close()
        w.line()

    def _period_over_period(self, w: _Writer, vm: _ViewModel, m: Metric, name: str) -> None:
        t = self.plan.time
        if not (self.settings.enable_period_over_period and t and t.table == vm.table.name and m.agg in ("sum", "count", "count_distinct")):
            return
        pop = f"{name}_vs_last_year"
        w.open(f"measure: {pop}")
        w.line(f'label: "{lookml_string(m.label)} vs last year"')
        w.line("type: period_over_period")
        w.line(f"based_on: {name}")
        w.line(f"based_on_time: {vm.time_groups[t.column]}_year")
        w.line("period: year")
        w.line("kind: relative_change")
        w.line("value_format_name: percent_1")
        w.close()
        w.line()
        self.extras.period_over_period.append(f"{vm.name}.{pop}")

    def _detail_set(self, vm: _ViewModel) -> list[str]:
        fields = [vm.pk or SURROGATE_PK]
        for c in vm.columns:
            if len(fields) >= 6:
                break
            if c.name == vm.pk or c.is_pii or c.name in vm.fk_columns:
                continue
            if c.is_time:
                fields.append(f"{vm.time_groups[c.name]}_date")
            elif c.is_string or c.is_bool:
                fields.append(c.name)
        return fields

    def view_file(self, vm: _ViewModel) -> str:
        t = vm.table
        w = _Writer()
        w.open(f"view: {vm.name}")
        w.line(f"sql_table_name: `{t.full_name}` ;;")
        if t.description:
            w.line(f"# {lookml_string(t.description)}")
        w.line()
        if not vm.pk:
            w.open(f"dimension: {SURROGATE_PK}")
            w.line("primary_key: yes")
            w.line("hidden: yes")
            w.line("type: string")
            w.line("sql: TO_JSON_STRING(${TABLE}) ;;")
            w.close()
            w.line()
            self.extras.surrogate_keys.append(t.name)
        for c in vm.columns:
            self._dimension(w, vm, c)
        w.open("measure: count")
        w.line(f'label: "Number of {lookml_string(t.name.replace("_", " "))}"')
        if any(m.agg == "count" for m, _ in vm.measures):
            w.line("hidden: yes")  # the plan's own count measure is the visible one
        w.line("type: count")
        w.line("drill_fields: [detail*]")
        w.close()
        w.line()
        for m, name in vm.measures:
            self._measure(w, vm, m, name)
            self._period_over_period(w, vm, m, name)
        self.extras.drill_fields = True
        w.open("set: detail")
        w.line(f"fields: [{', '.join(self._detail_set(vm))}]")
        w.close()
        w.close()
        return w.text()

    # ---------- model ----------
    def _filter_sql(self) -> str | None:
        parts = []
        ops = {"equals": "=", "not_equals": "!=", "greater_than": ">", "less_than": "<"}
        for f in self.plan.filters:
            vm = self.views[f.table]
            ref = f"${{{vm.name}.{vm.field_for(f.column)}}}"
            vals = [_sql_literal(v) for v in f.values]
            if f.operator in ("in", "not_in"):
                parts.append(f"{ref} {'NOT IN' if f.operator == 'not_in' else 'IN'} ({', '.join(vals)})")
            else:
                parts.append(f"{ref} {ops[f.operator]} {vals[0]}")
        return " AND ".join(parts) or None

    def _tests(self, w: _Writer, always_filter: str | None) -> None:
        months = self.settings.default_months
        flt = f'filters: [{always_filter}: "{months} months"]' if always_filter else None
        for vm in self.views.values():
            if not vm.pk or len(self.extras.tests) >= 4:
                continue
            name = f"{vm.name}_{vm.pk}_is_unique"
            w.open(f"test: {name}")
            w.open(f"explore_source: {self.explore}")
            w.open(f"column: {vm.pk}")
            w.line(f"field: {vm.name}.{vm.pk}")
            w.close()
            w.open("column: count")
            w.line(f"field: {vm.name}.count")
            w.close()
            w.line(f"sorts: [{vm.name}.count: desc]")
            w.line("limit: 1")
            if flt:
                w.line(flt)
            w.close()
            w.open(f"assert: {vm.pk}_is_unique")
            w.line(f"expression: ${{{vm.name}.count}} = 1 ;;")
            w.close()
            w.close()
            w.line()
            self.extras.tests.append(f"each {singular(vm.table.name).replace('_', ' ')} appears only once")
        for m in self.plan.metrics:
            if m.agg != "sum" or m.format != "currency":
                continue
            full = self.metric_fields[m.name]
            fld = full.split(".", 1)[1]
            w.open(f"test: {fld}_is_not_negative")
            w.open(f"explore_source: {self.explore}")
            w.open(f"column: {fld}")
            w.line(f"field: {full}")
            w.close()
            if flt:
                w.line(flt)
            w.close()
            w.open(f"assert: {fld}_is_not_negative")
            w.line(f"expression: ${{{full}}} >= 0 ;;")
            w.close()
            w.close()
            w.line()
            self.extras.tests.append(f"{m.label} is never negative")

    def model_file(self) -> str:
        ctx = self.ctx
        base = ctx.by_name[self.plan.base_table]
        w = _Writer()
        w.line(f'connection: "{lookml_string(ctx.connection_name)}"')
        w.line()
        if ctx.folder_layout == "flat":
            w.line('include: "*.view.lkml"')
            w.line('include: "*.dashboard.lookml"')
        else:
            w.line('include: "/views/*.view.lkml"')
            w.line('include: "/dashboards/*.dashboard.lookml"')
        w.line()
        dg = f"{snake(ctx.model_name)}_default_datagroup"
        w.open(f"datagroup: {dg}")
        w.line(f"sql_trigger: SELECT MAX(last_modified_time) FROM `{base.project}.{base.dataset}.__TABLES__` ;;")
        w.line('max_cache_age: "24 hours"')
        w.close()
        w.line()
        w.line(f"persist_with: {dg}")
        w.line()
        self.extras.datagroup = dg
        has_pii = any(c.is_pii for vm in self.views.values() for c in vm.columns)
        if has_pii:
            grant = self.settings.pii_access_grant
            w.open(f"access_grant: {grant}")
            w.line(f"user_attribute: {grant}")
            w.line('allowed_values: ["yes"]')
            w.close()
            w.line()
        w.open(f"explore: {self.explore}")
        w.line(f'label: "{lookml_string(self.plan.title)}"')
        where = self._filter_sql()
        if where:
            w.line(f"sql_always_where: {where} ;;")
        always = self._always_filter_field()
        if always:
            w.open("always_filter:")
            w.line(f'filters: [{always}: "{self.settings.default_months} months"]')
            w.close()
            self.extras.always_filter = always
        for j in self.joins:
            to_vm, from_vm = self.views[j.to_table], self.views[j.from_table]
            w.line()
            w.open(f"join: {to_vm.name}")
            w.line("type: left_outer")
            w.line(f"relationship: {j.relationship}")
            w.line(f"sql_on: ${{{from_vm.name}.{from_vm.field_for(j.from_col)}}} = ${{{to_vm.name}.{to_vm.field_for(j.to_col)}}} ;;")
            w.close()
            if j.fans_out:
                self.extras.fanout_joins.append(f"{j.from_table} → {j.to_table}")
        w.close()
        w.line()
        self._tests(w, always)
        return w.text()

    # ---------- dashboard ----------
    def _tile_field(self, dim: str | None) -> str | None:
        if not dim:
            return None
        t = self.plan.time
        if dim == "time" and t:
            vm = self.views[t.table]
            return f"{vm.name}.{vm.field_for(t.column, t.grain)}"
        if "." in dim:
            table, col = dim.split(".", 1)
            vm = self.views.get(table)
            if vm:
                return f"{vm.name}.{vm.field_for(col)}"
        return None

    def _element(self, tile: Tile, date_filter: str | None, pops: dict[str, str]) -> dict:
        metrics = [self.metric_fields[m] for m in tile.metrics if m in self.metric_fields]
        dim = self._tile_field(tile.dimension)
        el: dict = {"title": tile.title, "name": snake(tile.title)[:60], "model": snake(self.ctx.model_name), "explore": self.explore}
        if tile.type == "kpi":
            el["type"] = "single_value"
            fields = metrics[:1]
            if fields and fields[0] in pops:
                fields.append(pops[fields[0]])
                el.update({"show_comparison": True, "comparison_type": "change", "comparison_label": "vs last year"})
            el["fields"] = fields
        else:
            el["type"] = {"line": "looker_line", "bar": "looker_bar", "table": "looker_grid", "pie": "looker_pie"}[tile.type]
            el["fields"] = ([dim] if dim else []) + metrics
            if tile.type == "line" and dim:
                el["sorts"] = [f"{dim}"]
            elif metrics:
                el["sorts"] = [f"{metrics[0]} {tile.sort or 'desc'}"]
            el["limit"] = tile.limit or (500 if tile.type == "line" else 50)
        if date_filter:
            el["listen"] = {"Date": date_filter}
        return el

    def dashboard_file(self) -> tuple[str, str] | None:
        if not self.plan.tiles:
            return None
        name = snake(self.plan.title)[:80]
        t = self.plan.time
        date_filter = None
        if t:
            vm = self.views[t.table]
            date_filter = f"{vm.name}.{vm.field_for(t.column, 'date')}"
        pops = {}
        for full in self.extras.period_over_period:
            pops[full.removesuffix("_vs_last_year")] = full
        kpis = [x for x in self.plan.tiles if x.type == "kpi"][:4]
        charts = [x for x in self.plan.tiles if x.type != "kpi"]
        elements = []
        if kpis:
            width = 24 // len(kpis)
            for i, tile in enumerate(kpis):
                el = self._element(tile, date_filter, pops)
                el.update({"row": 0, "col": i * width, "width": width, "height": 4})
                elements.append(el)
        row = 4 if kpis else 0
        for i, tile in enumerate(charts):
            el = self._element(tile, date_filter, pops)
            last_alone = i == len(charts) - 1 and i % 2 == 0
            el.update({"row": row + (i // 2) * 8, "col": 0 if (i % 2 == 0) else 12, "width": 24 if last_alone else 12, "height": 8})
            elements.append(el)
        dash: dict = {
            "dashboard": name,
            "title": self.plan.title,
            "layout": "newspaper",
            "preferred_viewer": "dashboards-next",
            "crossfilter_enabled": True,
        }
        self.extras.crossfilter = True
        if date_filter:
            dash["filters"] = [{
                "name": "Date", "title": "Date", "type": "field_filter",
                "default_value": f"{self.settings.default_months} months", "allow_multiple_values": True, "required": False,
                "model": snake(self.ctx.model_name), "explore": self.explore, "field": date_filter,
            }]
        dash["elements"] = elements
        return name, yaml.safe_dump([dash], sort_keys=False, allow_unicode=True, default_flow_style=False, width=120)

    # ---------- package ----------
    def generate(self) -> GeneratedPackage:
        flat = self.ctx.folder_layout == "flat"
        files = []
        for vm in self.views.values():
            path = f"{vm.name}.view.lkml" if flat else f"views/{vm.name}.view.lkml"
            files.append(GenFile(path, "view", self.view_file(vm)))
        model = snake(self.ctx.model_name)
        dash = self.dashboard_file()  # before model so period-over-period list is complete
        files.append(GenFile(f"{model}.model.lkml" if flat else f"models/{model}.model.lkml", "model", self.model_file()))
        dash_name = None
        if dash:
            dash_name, content = dash
            files.append(GenFile(f"{dash_name}.dashboard.lookml" if flat else f"dashboards/{dash_name}.dashboard.lookml", "dashboard", content))
        return GeneratedPackage(files, self.explore, self.joins, self.metric_fields, self.extras, dash_name)


def generate(ctx: GenContext) -> GeneratedPackage:
    return Generator(ctx).generate()
