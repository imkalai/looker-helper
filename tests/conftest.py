import pytest

from app.config import get_settings
from app.schemas.plan import Dimension, Filter, Metric, Plan, Tile, TimeSpec
from app.services.metadata.demo import DemoMetadata

SALES_TABLES = ["orders", "order_items", "customers"]


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def sales_tables():
    return DemoMetadata().get_tables("acme-analytics", "sales", SALES_TABLES)


@pytest.fixture
def sales_plan():
    return Plan(
        title="Monthly sales by region",
        base_table="orders",
        metrics=[
            Metric(name="total_revenue", label="Total revenue", agg="sum", table="orders", column="amount", format="currency"),
            Metric(name="number_of_orders", label="Number of orders", agg="count", table="orders"),
            Metric(name="average_order_value", label="Average order value", agg="ratio", table="orders",
                   numerator="total_revenue", denominator="number_of_orders", format="currency"),
        ],
        dimensions=[Dimension(table="customers", column="region", label="Customer region")],
        time=TimeSpec(table="orders", column="created_at", grain="month"),
        filters=[Filter(table="orders", column="status", operator="not_equals", values=["cancelled"])],
        tiles=[
            Tile(type="kpi", title="Total revenue", metrics=["total_revenue"]),
            Tile(type="kpi", title="Number of orders", metrics=["number_of_orders"]),
            Tile(type="kpi", title="Average order value", metrics=["average_order_value"]),
            Tile(type="line", title="Revenue by month", metrics=["total_revenue"], dimension="time"),
            Tile(type="bar", title="Revenue by region", metrics=["total_revenue"], dimension="customers.region"),
        ],
    )
