"""The plan Cortex produces from a request. Python turns a validated plan into LookML."""

from typing import Literal

from pydantic import BaseModel, Field

Agg = Literal["sum", "count", "count_distinct", "average", "min", "max", "ratio"]
Fmt = Literal["number", "currency", "percent"]
Grain = Literal["date", "week", "month", "quarter", "year"]
Operator = Literal["equals", "not_equals", "in", "not_in", "greater_than", "less_than"]
TileType = Literal["kpi", "line", "bar", "table", "pie"]


class Metric(BaseModel):
    name: str = Field(description="snake_case identifier, e.g. total_revenue")
    label: str = Field(description="Plain-English name, e.g. Total revenue")
    agg: Agg
    table: str
    column: str | None = None
    numerator: str | None = Field(default=None, description="For ratio: name of another metric")
    denominator: str | None = Field(default=None, description="For ratio: name of another metric")
    format: Fmt = "number"
    description: str = ""


class Dimension(BaseModel):
    table: str
    column: str
    label: str = ""


class TimeSpec(BaseModel):
    table: str
    column: str
    grain: Grain = "month"


class Filter(BaseModel):
    table: str
    column: str
    operator: Operator
    values: list[str]
    label: str = ""


class Tile(BaseModel):
    type: TileType
    title: str
    metrics: list[str]
    dimension: str | None = Field(default=None, description='"time" or "table.column"')
    sort: Literal["asc", "desc"] | None = None
    limit: int | None = None


class Plan(BaseModel):
    title: str
    base_table: str
    metrics: list[Metric]
    dimensions: list[Dimension] = Field(default_factory=list)
    time: TimeSpec | None = None
    filters: list[Filter] = Field(default_factory=list)
    tiles: list[Tile] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def metric(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)
