import io
import zipfile

import lkml
import yaml

from app.services.lookml.generator import GenContext, generate
from app.services.lookml.relations import join_tree, primary_key
from app.services.lookml.validate import validate_files
from app.services.packaging import build_zip


def _gen(plan, tables, layout="standard"):
    return generate(GenContext(plan=plan, tables=tables, model_name="sales", connection_name="bq_acme_analytics", folder_layout=layout))


def test_primary_keys_and_joins(sales_tables):
    by = {t.name: t for t in sales_tables}
    assert primary_key(by["orders"]) == "order_id"
    assert primary_key(by["order_items"]) == "order_item_id"
    steps = {(s.from_table, s.to_table): s.relationship for s in join_tree("orders", sales_tables)}
    assert steps[("orders", "customers")] == "many_to_one"
    assert steps[("orders", "order_items")] == "one_to_many"


def test_generates_valid_files(sales_plan, sales_tables):
    pkg = _gen(sales_plan, sales_tables)
    paths = [f.path for f in pkg.files]
    assert "views/orders.view.lkml" in paths
    assert "models/sales.model.lkml" in paths
    assert "dashboards/monthly_sales_by_region.dashboard.lookml" in paths
    assert validate_files(pkg.files) == []


def test_best_practices_applied(sales_plan, sales_tables):
    pkg = _gen(sales_plan, sales_tables)
    files = {f.path: f.content for f in pkg.files}
    model = lkml.load(files["models/sales.model.lkml"])
    explore = model["explores"][0]
    assert "cancelled" in explore["sql_always_where"]
    assert explore["always_filter"]["filters__all"]  # 1.2 TB partitioned table
    assert model["datagroups"][0]["name"] == "sales_default_datagroup"
    assert model["access_grants"][0]["name"] == "can_see_pii"
    assert any(t["name"].endswith("_is_unique") for t in model["tests"])
    customers = files["views/customers.view.lkml"]
    assert "required_access_grants: [can_see_pii]" in customers
    orders = files["views/orders.view.lkml"]
    assert "type: period_over_period" in orders
    dash = yaml.safe_load(files["dashboards/monthly_sales_by_region.dashboard.lookml"])[0]
    assert dash["crossfilter_enabled"] is True
    kpi = dash["elements"][0]
    assert kpi["show_comparison"] is True
    assert pkg.extras.fanout_joins  # orders -> order_items


def test_flat_layout_and_zip(sales_plan, sales_tables):
    pkg = _gen(sales_plan, sales_tables, layout="flat")
    model = next(f for f in pkg.files if f.kind == "model")
    assert 'include: "*.view.lkml"' in model.content
    data = build_zip([(f.path, f.content) for f in pkg.files])
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "orders.view.lkml" in names


def test_validator_catches_bad_reference(sales_plan, sales_tables):
    pkg = _gen(sales_plan, sales_tables)
    dash = next(f for f in pkg.files if f.kind == "dashboard")
    dash.content = dash.content.replace("orders.total_revenue", "orders.no_such_field", 1)
    assert any("no_such_field" in e for e in validate_files(pkg.files))
