"""Primary keys and joins inferred from table and column names."""

from collections import deque
from dataclasses import dataclass

from app.schemas.meta import TableMeta
from app.services.text import singular


@dataclass(frozen=True)
class Relation:
    """child.child_col -> parent.parent_col (many child rows per parent row)."""

    child: str
    child_col: str
    parent: str
    parent_col: str


@dataclass(frozen=True)
class JoinStep:
    from_table: str
    to_table: str
    from_col: str
    to_col: str
    relationship: str  # many_to_one | one_to_many

    @property
    def fans_out(self) -> bool:
        return self.relationship == "one_to_many"


def primary_key(table: TableMeta) -> str | None:
    one = singular(table.name)
    names = {c.name.lower(): c for c in table.columns if c.is_supported}
    for candidate in (f"{one}_id", f"{table.name}_id", "id"):
        if candidate in names:
            return names[candidate].name
    if one in names and names[one].is_string:
        return names[one].name
    return None


def infer_relations(tables: list[TableMeta]) -> list[Relation]:
    pks = {t.name: primary_key(t) for t in tables}
    rels: list[Relation] = []
    for child in tables:
        child_cols = {c.name.lower(): c.name for c in child.columns}
        for parent in tables:
            if parent.name == child.name:
                continue
            pk = pks[parent.name]
            if not pk:
                continue
            key = pk.lower() if pk.lower() != "id" else f"{singular(parent.name)}_id"
            if key in child_cols and child_cols[key].lower() != (pks[child.name] or "").lower():
                rels.append(Relation(child.name, child_cols[key], parent.name, pk))
    return rels


def join_tree(base: str, tables: list[TableMeta]) -> list[JoinStep]:
    """Breadth-first joins from the base table to every table it can reach."""
    rels = infer_relations(tables)
    adj: dict[str, list[JoinStep]] = {t.name: [] for t in tables}
    for r in rels:
        adj[r.child].append(JoinStep(r.child, r.parent, r.child_col, r.parent_col, "many_to_one"))
        adj[r.parent].append(JoinStep(r.parent, r.child, r.parent_col, r.child_col, "one_to_many"))
    seen = {base}
    steps: list[JoinStep] = []
    queue = deque([base])
    while queue:
        current = queue.popleft()
        # prefer many_to_one joins (they never multiply rows)
        for step in sorted(adj.get(current, []), key=lambda s: s.relationship != "many_to_one"):
            if step.to_table not in seen:
                seen.add(step.to_table)
                steps.append(step)
                queue.append(step.to_table)
    return steps


def reachable(base: str, tables: list[TableMeta]) -> dict[str, int]:
    """Table name -> number of joins needed from the base table."""
    depth = {base: 0}
    for step in join_tree(base, tables):
        depth[step.to_table] = depth[step.from_table] + 1
    return depth
