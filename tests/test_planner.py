"""The mock Cortex rules and the planner's validation."""

import pytest

from app.schemas.plan import Metric, Plan
from app.services import planner
from app.services.cortex.mock import MockCortex
from app.services.metadata.demo import DemoMetadata

ALL_SALES = ["orders", "order_items", "customers", "products", "regions", "returns"]


@pytest.fixture
def sales():
    return DemoMetadata().get_tables("acme-analytics", "sales", ALL_SALES)


@pytest.fixture
def crm():
    return DemoMetadata().get_tables("acme-analytics", "crm", ["accounts", "churn_events"])


def test_revenue_by_region_by_month(sales):
    p = planner.make_plan(MockCortex(), "Revenue by region for each month, as a dashboard", sales)
    assert p.base_table == "orders"
    assert [(m.agg, m.column) for m in p.metrics] == [("sum", "amount")]
    assert [(d.table, d.column) for d in p.dimensions] == [("customers", "region")]
    assert p.time.column == "created_at" and p.time.grain == "month"
    assert p.title == "Revenue by region for each month"


def test_aov_and_exclusion(sales):
    p = planner.make_plan(MockCortex(), "revenue, number of orders and average order value by channel, excluding cancelled orders", sales)
    assert {m.name for m in p.metrics} == {"total_revenue", "number_of_orders", "average_order_value"}
    assert p.filters[0].column == "status" and p.filters[0].values == ["cancelled"]


def test_revenue_by_product_uses_line_amounts(sales):
    p = planner.make_plan(MockCortex(), "Top 10 products by revenue", sales)
    assert p.base_table == "order_items" and p.metrics[0].column == "line_amount"
    assert p.tiles[-1].limit == 10


def test_dimension_ignores_table_prefix(crm):
    p = planner.make_plan(MockCortex(), "Number of churn events by reason each month", crm)
    assert [(d.table, d.column) for d in p.dimensions] == [("churn_events", "churn_reason")]


def test_suggest_tables_includes_join_path(sales):
    infos = DemoMetadata().list_tables("acme-analytics", "sales")
    assert planner.suggest_tables(MockCortex(), "Top 10 products by revenue", sales, infos) == ["order_items", "products"]


@pytest.mark.parametrize("text,intent", [
    ("Yes, but leave out cancelled orders.", "change_and_build"),
    ("looks good", "build"),
    ("What does each metric mean?", "question"),
    ("make it weekly", "change"),
])
def test_classify(sales, text, intent):
    plan = planner.make_plan(MockCortex(), "revenue by month", sales)
    assert planner.classify(MockCortex(), text, plan, sales) == intent


def test_validation_rejects_invented_columns(sales):
    bad = Plan(title="x", base_table="orders", metrics=[Metric(name="m", label="M", agg="sum", table="orders", column="profit")])
    _, errors = planner.validate_plan(bad, sales)
    assert any("profit" in e for e in errors)


def test_validation_drops_personal_data_breakdowns(sales):
    from app.schemas.plan import Dimension

    p = Plan(title="x", base_table="orders", metrics=[Metric(name="n", label="N", agg="count", table="orders")],
             dimensions=[Dimension(table="customers", column="email")])
    p, errors = planner.validate_plan(p, sales)
    assert not errors and p.dimensions == [] and p.notes


@pytest.mark.parametrize("value", ["x' ;; } sql_always_where: 1=1", "{{ _user_attributes['x'] }}", "${orders.id}", "a\nb", ""])
def test_unsafe_filter_values_rejected(sales, value):
    from app.schemas.plan import Filter

    p = Plan(title="x", base_table="orders", metrics=[Metric(name="n", label="N", agg="count", table="orders")],
             filters=[Filter(table="orders", column="status", operator="not_equals", values=[value])])
    _, errors = planner.validate_plan(p, sales)
    assert errors


def test_sql_literal_escaping():
    from app.services.lookml.generator import _sql_literal

    bs = chr(92)  # a backslash
    assert _sql_literal("o'brien" + bs) == "'o" + bs + "'brien" + bs + bs + "'"  # quote and backslash both escaped
    assert _sql_literal("42") == "42" and _sql_literal("nan") == "'nan'"
