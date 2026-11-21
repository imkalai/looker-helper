"""Check generated files: LookML syntax, dashboard YAML, and that every field reference exists."""

import re

import lkml
import yaml

from app.services.lookml.generator import GenFile

_REF = re.compile(r"\$\{([A-Za-z0-9_]+)(?:\.([A-Za-z0-9_]+))?\}")


def _view_fields(view: dict) -> set[str]:
    fields = {d["name"] for d in view.get("dimensions", [])}
    fields |= {m["name"] for m in view.get("measures", [])}
    for g in view.get("dimension_groups", []):
        fields.add(g["name"])
        for tf in g.get("timeframes", []):
            fields.add(f"{g['name']}_{tf}")
    return fields


def validate_files(files: list[GenFile]) -> list[str]:
    errors: list[str] = []
    views: dict[str, set[str]] = {}
    parsed_views: list[dict] = []
    models: list[dict] = []
    dashboards: list[tuple[str, list]] = []

    for f in files:
        try:
            if f.kind == "dashboard":
                data = yaml.safe_load(f.content)
                if not isinstance(data, list) or not data or "dashboard" not in data[0]:
                    errors.append(f"{f.path}: not a LookML dashboard")
                else:
                    dashboards.append((f.path, data))
                continue
            parsed = lkml.load(f.content)
        except Exception as exc:  # syntax error
            errors.append(f"{f.path}: syntax error ({exc})")
            continue
        for v in parsed.get("views", []):
            views[v["name"]] = _view_fields(v)
            parsed_views.append(v)
        if f.kind == "model":
            models.append(parsed)

    def check_ref(where: str, own_view: str | None, text: str) -> None:
        for a, b in _REF.findall(text or ""):
            if a == "TABLE" and not b:
                continue
            view, fld = (a, b) if b else (own_view, a)
            if view not in views:
                errors.append(f"{where}: unknown view '{view}' in ${{{a}{'.' + b if b else ''}}}")
            elif fld not in views[view]:
                errors.append(f"{where}: unknown field '{view}.{fld}'")

    for v in parsed_views:
        for kind in ("dimensions", "dimension_groups", "measures"):
            for item in v.get(kind, []):
                check_ref(f"view {v['name']}.{item['name']}", v["name"], item.get("sql", ""))
                for p in ("based_on",):
                    if p in item and item[p] not in views[v["name"]]:
                        errors.append(f"view {v['name']}.{item['name']}: unknown {p} '{item[p]}'")

    explores: set[str] = set()
    for m in models:
        for e in m.get("explores", []):
            explores.add(e["name"])
            if e["name"] not in views:
                errors.append(f"explore {e['name']}: no view with that name")
            check_ref(f"explore {e['name']}", None, e.get("sql_always_where", ""))
            for j in e.get("joins", []):
                if j["name"] not in views:
                    errors.append(f"explore {e['name']}: join to unknown view '{j['name']}'")
                check_ref(f"join {j['name']}", None, j.get("sql_on", ""))

    def check_field(where: str, full: str) -> None:
        name = full.split(" ")[0]
        if "." not in name:
            errors.append(f"{where}: field '{name}' has no view name")
            return
        view, fld = name.split(".", 1)
        if view not in views or fld not in views[view]:
            errors.append(f"{where}: unknown field '{name}'")

    for path, data in dashboards:
        dash = data[0]
        for flt in dash.get("filters", []) or []:
            if flt.get("field"):
                check_field(f"{path} filter {flt.get('name')}", flt["field"])
        for el in dash.get("elements", []) or []:
            where = f"{path} tile '{el.get('title')}'"
            if el.get("explore") not in explores:
                errors.append(f"{where}: unknown explore '{el.get('explore')}'")
            for fld in el.get("fields", []) or []:
                check_field(where, fld)
            for s in el.get("sorts", []) or []:
                check_field(where, s)
            for fld in (el.get("listen") or {}).values():
                check_field(where, fld)
    return errors
