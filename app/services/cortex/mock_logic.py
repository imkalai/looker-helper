"""Keyword rules that stand in for Cortex during development.

They read a plain-English request against the session's table details and produce the same JSON a real
LLM is asked for. They're deliberately simple; real Cortex replaces them via LH_CORTEX_MODE=real.
"""

import re

from app.schemas.meta import ColumnMeta, TableMeta
from app.schemas.plan import Dimension, Filter, Metric, Plan, Tile, TimeSpec
from app.services.lookml.relations import infer_relations, join_tree, primary_key, reachable
from app.services.text import humanize, singular, snake, words

MONEY_WORDS = ["revenue", "sales", "turnover", "income", "takings", "spend", "amount", "value", "gmv", "fees", "fee"]
MONEY_HINTS = ["revenue", "sales", "amount", "total", "value", "spend", "fee", "balance", "cost", "price"]
GRAINS = {
    "daily": "date", "day": "date", "days": "date", "weekly": "week", "week": "week", "weeks": "week",
    "monthly": "month", "month": "month", "months": "month", "quarterly": "quarter", "quarter": "quarter",
    "quarters": "quarter", "yearly": "year", "annual": "year", "annually": "year", "year": "year", "years": "year",
}
TREND_WORDS = {"trend", "trends", "timeline", "history", "historical", "over time"}
APPROVE = re.compile(r"^\s*(yes|yep|yeah|yup|ok|okay|sure|perfect|great|good|looks good|sounds good|go ahead|go for it|do it|build( it)?|confirm(ed)?|that'?s (right|fine|good)|correct|lgtm)\b")
QUESTION = re.compile(r"^\s*(what|how|why|which|where|who|when|explain|describe|tell me|can you explain|does|is|are|do)\b")
EXCLUDE = re.compile(r"\b(?:exclude|excluding|leave out|leaving out|without|ignore|ignoring|remove|not including|except|filter out|drop)\s+(?:the\s+|all\s+|any\s+)?([a-z][a-z_]*)")
ONLY = re.compile(r"\b(?:only|just)\s+(?:show\s+)?(?:the\s+)?([a-z][a-z_]*)")
BY_PHRASE = re.compile(r"\b(?:by|per|for each|for every|across|split by|broken down by|grouped by|for)\s+([a-z_ ,]+?)(?=\s+(?:by|per|for|over|in|and then|as|with|from|across|excluding|without|but|on|including|vs|versus)\b|[.;!?]|$)")
TOP_N = re.compile(r"\btop\s+(\d{1,3})\s+([a-z_]+)")
STOP = {"the", "a", "an", "each", "every", "month", "months", "week", "weeks", "day", "days", "year", "years", "quarter",
        "quarters", "time", "date", "dashboard", "chart", "me", "us", "it", "this", "that", "them", "all"}


def _sing_words(text: str) -> set[str]:
    return {singular(w) for w in words(text)}


def _clean(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\b(vs\.?|versus|compared (to|with)|compare (to|with)|against)\s+(the\s+)?(last|previous|prior)\s+(year|month|week|quarter)\b", " ", t)
    t = re.sub(r"\b(last|previous|prior|this)\s+(year|month|week|quarter)\b", " ", t)
    return t


class Rules:
    def __init__(self, tables: list[TableMeta]):
        self.tables = tables
        self.by = {t.name: t for t in tables}
        self.fks = {(r.child, r.child_col) for r in infer_relations(tables)}

    # ---------- helpers ----------
    def _is_id(self, t: TableMeta, c: ColumnMeta) -> bool:
        return c.name == primary_key(t) or (t.name, c.name) in self.fks or c.name.lower().endswith("_id") or c.name.lower() == "id"

    def _table_score(self, text: str) -> dict[str, int]:
        sw = _sing_words(text)
        scores = {}
        for t in self.tables:
            name_words = {singular(w) for w in t.name.split("_")}
            s = 3 if name_words <= sw else 0
            s += 2 if t.partition_field else 0
            s += 1 if any(c.is_time for c in t.columns) else 0
            scores[t.name] = s
        return scores

    def _ordered_tables(self, text: str) -> list[TableMeta]:
        sc = self._table_score(text)
        return sorted(self.tables, key=lambda t: (-sc[t.name], self.tables.index(t)))

    def _is_dim_candidate(self, c: ColumnMeta) -> bool:
        n = c.name.lower()
        return not (c.is_pii or c.is_time or c.is_numeric or n == "id" or n.endswith("_id"))

    def m2o(self, start: str) -> set[str]:
        """Tables reachable from `start` without multiplying rows (only many-to-one joins)."""
        rels = infer_relations(self.tables)
        seen, stack = {start}, [start]
        while stack:
            cur = stack.pop()
            for r in rels:
                if r.child == cur and r.parent not in seen:
                    seen.add(r.parent)
                    stack.append(r.parent)
        return seen

    def _money_column(self, text: str, only: set[str] | None = None) -> tuple[TableMeta, ColumnMeta] | None:
        allow_refund = "refund" in text
        allow_price = "price" in text
        best = None
        for rank, t in enumerate(self._ordered_tables(text)):
            if only is not None and t.name not in only:
                continue
            for c in t.columns:
                if not c.is_numeric or self._is_id(t, c) or c.is_pii:
                    continue
                n = c.name.lower()
                if ("refund" in n and not allow_refund) or ("price" in n and not allow_price):
                    continue
                hint = next((i for i, h in enumerate(MONEY_HINTS) if h in n), None)
                if hint is None:
                    continue
                exact = 0 if n in MONEY_HINTS else 1
                key = (rank, exact, hint)
                if best is None or key < best[0]:
                    best = (key, t, c)
        return (best[1], best[2]) if best else None

    def _table_for_phrase(self, phrase: str) -> TableMeta | None:
        pw = _sing_words(phrase)
        for t in self.tables:
            if {singular(w) for w in t.name.split("_")} <= pw:
                return t
        return None

    def _label_column(self, t: TableMeta) -> ColumnMeta | None:
        strings = [c for c in t.columns if (c.is_string) and not c.is_pii and not self._is_id(t, c)]
        for key in ("name", "title", "label"):
            for c in strings:
                if key in c.name.lower():
                    return c
        pk = primary_key(t)
        return strings[0] if strings else (t.column(pk) if pk else None)

    def _time_column(self, t: TableMeta) -> ColumnMeta | None:
        if t.partition_field:
            c = t.column(t.partition_field)
            if c and c.is_time:
                return c
        times = [c for c in t.columns if c.is_time and not c.is_pii]
        for key in ("created", "order", "date", "_at", "start", "time"):
            for c in times:
                if key in c.name.lower():
                    return c
        return times[0] if times else None

    # ---------- extraction ----------
    def metrics(self, text: str) -> list[Metric]:
        t = _clean(text)
        out: list[Metric] = []

        def add(m: Metric):
            if not any(x.name == m.name for x in out):
                out.append(m)

        def money_metric() -> Metric | None:
            found = self._money_column(t)
            if not found:
                return None
            table, col = found
            word = next((w for w in MONEY_WORDS if re.search(rf"\b{w}\b", t)), None)
            if word in (None, "amount", "value", "fee", "fees"):
                name, label = f"total_{col.name}", f"Total {col.name.replace('_', ' ')}"
            else:
                name, label = f"total_{word}", f"Total {word}"
            return Metric(name=name, label=label, agg="sum", table=table.name, column=col.name, format="currency")

        def count_metric(table: TableMeta) -> Metric:
            noun = table.name.replace("_", " ")
            return Metric(name=f"number_of_{table.name}", label=f"Number of {noun}", agg="count", table=table.name)

        aov = re.search(r"\b(average|avg|mean)\s+(order|basket|transaction|sale)\s+(value|size)\b|\baov\b", t)
        if any(re.search(rf"\b{w}\b", t) for w in MONEY_WORDS) or aov:
            m = money_metric()
            if m:
                add(m)
        for phrase in re.findall(r"(?:number of|count of|how many|volume of|total number of|no\.? of)\s+([a-z_ ]+?)(?=\s+(?:by|per|for|and|over|across|in|each)\b|[,.;]|$)", t):
            table = self._table_for_phrase(phrase)
            if table:
                add(count_metric(table))
        for word in re.findall(r"\b([a-z_]+)\s+count\b", t):
            table = self._table_for_phrase(word)
            if table:
                add(count_metric(table))
        for word in re.findall(r"\b(?:unique|distinct|different)\s+([a-z_]+)", t):
            key = f"{singular(word)}_id"
            for tb in self._ordered_tables(t):
                if tb.column(key) and primary_key(tb) != key:
                    add(Metric(name=f"unique_{word}", label=f"Unique {word}", agg="count_distinct", table=tb.name, column=key))
                    break
            else:
                table = self._table_for_phrase(word)
                if table:
                    add(count_metric(table))
        if aov:
            money = next((m for m in out if m.agg == "sum" and m.format == "currency"), None)
            if money:
                order_table = self._table_for_phrase(aov.group(2) or "order") or self.by[money.table]
                cnt = next((m for m in out if m.agg == "count" and m.table == order_table.name), None)
                if not cnt:
                    cnt = count_metric(order_table)
                    add(cnt)
                add(Metric(name="average_order_value", label="Average order value", agg="ratio", table=money.table,
                           numerator=money.name, denominator=cnt.name, format="currency"))
        for phrase in re.findall(r"\b(?:average|avg|mean)\s+([a-z_ ]+?)(?=\s+(?:by|per|for|and|over|across|in)\b|[,.;]|$)", t):
            if re.match(r"(order|basket|transaction|sale)\s+(value|size)", phrase):
                continue
            pw = _sing_words(phrase)
            for tb in self._ordered_tables(t):
                col = next((c for c in tb.columns if c.is_numeric and not self._is_id(tb, c) and {singular(x) for x in c.name.split("_")} & pw), None)
                if col:
                    add(Metric(name=f"average_{col.name}", label=f"Average {col.name.replace('_', ' ')}", agg="average",
                               table=tb.name, column=col.name, format="currency" if any(h in col.name for h in MONEY_HINTS) else "number"))
                    break
        if re.search(r"\b(quantity|units sold|units|items sold)\b", t):
            for tb in self._ordered_tables(t):
                col = next((c for c in tb.columns if c.is_numeric and re.search(r"quantity|units|qty", c.name)), None)
                if col:
                    add(Metric(name=f"total_{col.name}", label=f"Total {col.name.replace('_', ' ')}", agg="sum", table=tb.name, column=col.name))
                    break
        used_cols = {(m.table, m.column) for m in out}
        for tb in self.tables:
            for c in tb.columns:
                if c.is_numeric and not self._is_id(tb, c) and (tb.name, c.name) not in used_cols:
                    phrase = c.name.replace("_", " ")
                    if len(phrase) > 3 and re.search(rf"\b{re.escape(phrase)}\b", t):
                        add(Metric(name=f"total_{c.name}", label=f"Total {phrase}", agg="sum", table=tb.name, column=c.name,
                                   format="currency" if any(h in c.name for h in MONEY_HINTS) else "number"))
        return out

    def default_metrics(self, text: str) -> list[Metric]:
        tb = self._ordered_tables(text)[0]
        out = [Metric(name=f"number_of_{tb.name}", label=f"Number of {tb.name.replace('_', ' ')}", agg="count", table=tb.name)]
        money = self._money_column(text)
        if money and money[0].name == tb.name:
            out.insert(0, Metric(name=f"total_{money[1].name}", label=f"Total {money[1].name.replace('_', ' ')}", agg="sum",
                                 table=tb.name, column=money[1].name, format="currency"))
        return out

    def dimensions(self, text: str, base: str, exclude_values: set[str]) -> list[Dimension]:
        t = _clean(text)
        depth = reachable(base, self.tables)
        phrases = []
        for raw in BY_PHRASE.findall(t):
            phrases += [p.strip() for p in re.split(r",|\band\b|\bor\b", raw) if p.strip()]
        for n, word in TOP_N.findall(t):
            phrases.append(word)
        out: list[Dimension] = []
        for phrase in phrases:
            pw = _sing_words(phrase) - STOP
            if not pw or pw & {singular(v) for v in exclude_values}:
                continue
            candidates = []
            for tb in self.tables:
                if tb.name not in depth:
                    continue
                for c in tb.columns:
                    if not self._is_dim_candidate(c):
                        continue
                    cw = {singular(x) for x in c.name.split("_")}
                    own = cw - {singular(x) for x in tb.name.split("_")}  # churn_reason in churn_events -> {reason}
                    if cw <= pw or singular(c.name) in pw or (own and own <= pw):
                        candidates.append((depth[tb.name], len(cw), tb, c))
            if candidates:
                candidates.sort(key=lambda x: (x[0], x[1]))
                _, _, tb, c = candidates[0]
            else:
                tb = self._table_for_phrase(phrase)
                if not tb or tb.name not in depth or tb.name == base:
                    continue
                c = self._label_column(tb)
                if not c:
                    continue
            if not any(d.table == tb.name and d.column == c.name for d in out):
                label = humanize(c.name) if tb.name == base or singular(tb.name) in c.name else f"{humanize(singular(tb.name))} {c.name.replace('_', ' ')}"
                out.append(Dimension(table=tb.name, column=c.name, label=label))
        return out[:4]

    def grain(self, text: str) -> str | None:
        t = _clean(text)
        for w in words(t):
            if w in GRAINS:
                return GRAINS[w]
        if any(w in t for w in TREND_WORDS):
            return "month"
        return None

    def time_spec(self, base: str, grain: str) -> TimeSpec | None:
        tb = self.by[base]
        col = self._time_column(tb)
        if col:
            return TimeSpec(table=base, column=col.name, grain=grain)
        for name in reachable(base, self.tables):
            col = self._time_column(self.by[name])
            if col:
                return TimeSpec(table=name, column=col.name, grain=grain)
        return None

    def filters(self, text: str, base: str) -> list[Filter]:
        t = text.lower()
        depth = reachable(base, self.tables)
        out: list[Filter] = []

        def find_column(value: str, require_sample: bool) -> tuple[TableMeta, ColumnMeta, str] | None:
            v = singular(value)
            for tb in sorted(self.tables, key=lambda x: depth.get(x.name, 99)):
                if tb.name not in depth:
                    continue
                for c in tb.columns:
                    for s in c.sample_values:
                        sl = s.lower()
                        if sl == value or sl == v or sl.startswith(v) or v.startswith(sl):
                            return tb, c, s
            if require_sample:
                return None
            tb = self.by[base]
            for c in tb.columns:
                if c.is_string and re.search(r"(^|_)(status|state|type)$", c.name):
                    return tb, c, value
            return None

        for value in EXCLUDE.findall(t):
            found = find_column(value, require_sample=False)
            if found:
                tb, c, v = found
                out.append(Filter(table=tb.name, column=c.name, operator="not_equals", values=[v]))
        for value in ONLY.findall(t):
            found = find_column(value, require_sample=True)
            if found:
                tb, c, v = found
                out.append(Filter(table=tb.name, column=c.name, operator="equals", values=[v]))
        return out

    # ---------- plans ----------
    def tiles(self, plan: Plan, top_n: int | None) -> list[Tile]:
        tiles = [Tile(type="kpi", title=m.label, metrics=[m.name]) for m in plan.metrics[:4]]
        lead = plan.metrics[0]
        if plan.time:
            tiles.append(Tile(type="line", title=f"{lead.label} by {plan.time.grain if plan.time.grain != 'date' else 'day'}",
                              metrics=[lead.name], dimension="time"))
        for d in plan.dimensions:
            title = f"{lead.label} by {(d.label or humanize(d.column)).lower()}"
            if top_n:
                title = f"Top {top_n} {(d.label or humanize(d.column)).lower()} by {lead.label.lower()}"
            tiles.append(Tile(type="bar", title=title, metrics=[lead.name], dimension=f"{d.table}.{d.column}", sort="desc", limit=top_n))
        if not plan.time and not plan.dimensions:
            tiles.append(Tile(type="table", title="Summary", metrics=[m.name for m in plan.metrics]))
        return tiles

    def title(self, request: str, plan: Plan) -> str:
        t = request.strip().rstrip(".!?")
        t = re.sub(r"^(please\s+)?(can you\s+|could you\s+)?(show me|show|give me|i need|i want|i'd like|create|build|make|get)\s+(me\s+)?(a\s+|an\s+|the\s+)?", "", t, flags=re.I)
        t = re.sub(r"[,]?\s*(as|in|on|into)\s+(a|one)\s+dashboard.*$", "", t, flags=re.I)
        t = re.sub(r"^dashboard\s+(of|for|with|showing)\s+", "", t, flags=re.I)
        t = t.strip(" ,")
        if 4 <= len(t) <= 70:
            return t[:1].upper() + t[1:]
        lead = plan.metrics[0].label
        if plan.dimensions:
            return f"{lead} by {plan.dimensions[0].label.lower() or plan.dimensions[0].column}"
        return lead

    def plan_from_request(self, request: str) -> Plan:
        metrics = self.metrics(request) or self.default_metrics(request)
        lead = next((m for m in metrics if m.agg != "ratio"), metrics[0])
        base = lead.table
        depth = reachable(base, self.tables)
        notes = []
        kept = [m for m in metrics if m.table in depth]
        if len(kept) < len(metrics):
            notes.append("Some measures were left out because their tables can't be joined to " + humanize(base).lower() + ".")
        filters = self.filters(request, base)
        excluded = {v.lower() for f in filters for v in f.values}
        dims = self.dimensions(request, base, excluded)
        dim_tables = {d.table for d in dims}
        if dims and not dim_tables <= self.m2o(base):
            # e.g. "revenue by product": use a money column whose table reaches products without multiplying rows
            fits = {t.name for t in self.tables if dim_tables <= self.m2o(t.name)}
            alt = self._money_column(_clean(request), only=fits)
            if alt:
                for i, m in enumerate(kept):
                    if m.agg == "sum" and m.format == "currency" and m.table == base:
                        kept[i] = m.model_copy(update={"table": alt[0].name, "column": alt[1].name})
                if lead.agg == "sum" and lead.format == "currency":
                    base = alt[0].name
                    dims = self.dimensions(request, base, excluded)
        grain = self.grain(request)
        time = self.time_spec(base, grain) if grain else None
        plan = Plan(title="", base_table=base, metrics=kept, dimensions=dims, time=time, filters=filters, notes=notes)
        top = TOP_N.search(_clean(request))
        plan.tiles = self.tiles(plan, int(top.group(1)) if top else None)
        plan.title = self.title(request, plan)
        return plan

    def revise(self, plan: Plan, message: str) -> Plan:
        p = plan.model_copy(deep=True)
        t = message.lower()
        removed_any = False
        for phrase in re.findall(r"\b(?:remove|drop|delete|take out|get rid of|no need for|don't need|do not need)\s+(?:the\s+)?([a-z_ ]+?)(?=\s+(?:chart|tile|metric|measure|breakdown)\b|[,.;]|$)", t):
            pw = _sing_words(phrase)
            before = (len(p.metrics), len(p.dimensions))
            p.metrics = [m for m in p.metrics if not ({singular(w) for w in words(m.label)} <= pw or _sing_words(m.label) & pw == _sing_words(m.label))] or p.metrics
            p.dimensions = [d for d in p.dimensions if not (_sing_words(d.label or d.column) <= pw)]
            if p.time and pw & {"time", "month", "trend", "date", "week", "monthly"}:
                p.time = None
            removed_any = removed_any or before != (len(p.metrics), len(p.dimensions))
        if not removed_any:
            for f in self.filters(message, p.base_table):
                if not any(x.column == f.column and x.values == f.values for x in p.filters):
                    p.filters.append(f)
        names = {m.name for m in p.metrics}
        for m in self.metrics(message):
            depth = reachable(p.base_table, self.tables)
            if m.name not in names and m.table in depth:
                p.metrics.append(m)
                names.add(m.name)
        excluded = {v.lower() for f in p.filters for v in f.values}
        for d in self.dimensions(message, p.base_table, excluded):
            if not any(x.table == d.table and x.column == d.column for x in p.dimensions):
                p.dimensions.append(d)
        grain = self.grain(message)
        if grain:
            p.time = p.time.model_copy(update={"grain": grain}) if p.time else self.time_spec(p.base_table, grain)
        top = TOP_N.search(_clean(message))
        top_n = int(top.group(1)) if top else next((x.limit for x in plan.tiles if x.limit), None)
        p.tiles = self.tiles(p, top_n)
        return p

    def suggest_tables(self, request: str) -> list[str]:
        plan = self.plan_from_request(request)
        used = {plan.base_table} | {m.table for m in plan.metrics} | {d.table for d in plan.dimensions} | {f.table for f in plan.filters}
        if plan.time:
            used.add(plan.time.table)
        parent = {s.to_table: s.from_table for s in join_tree(plan.base_table, self.tables)}
        needed = set()
        for name in used:
            while name and name not in needed:
                needed.add(name)
                name = parent.get(name)
        return [t.name for t in self.tables if t.name in needed]


def classify(message: str, has_plan: bool) -> str:
    t = message.strip().lower()
    approve = bool(APPROVE.match(t))
    question = bool(QUESTION.match(t)) or (t.endswith("?") and not approve)
    rest = APPROVE.sub("", t).strip(" ,.!-") if approve else t
    rest = re.sub(r"^(but|and|just|please|also)\b", "", rest).strip(" ,.")
    if not has_plan:
        return "question" if question and not re.search(r"\b(show|build|create|make|dashboard|chart)\b", t) else "new_plan"
    if question and not approve:
        return "question"
    if approve:
        return "change_and_build" if len(rest) > 3 else "build"
    return "change"


def explain(question: str, tables: list[TableMeta], plan: Plan | None) -> str:
    q = question.lower()
    if plan and re.search(r"\b(metric|measure|mean|means|calculated|number)s?\b", q):
        from app.services.lookml.report import metric_text

        lines = [f"• {m.label}: {metric_text(m, plan)}." for m in plan.metrics]
        return "Here's what each number means:\n" + "\n".join(lines)
    if re.search(r"\bjoin|combine|connected|link|relationship", q):
        rels = infer_relations(tables)
        if not rels:
            return "These tables don't share an ID column, so they can't be combined."
        lines = [f"• Each row in {humanize(r.child).lower()} links to one row in {humanize(r.parent).lower()} using {r.child_col.replace('_', ' ')}." for r in rels]
        return "Here's how your tables connect:\n" + "\n".join(lines)
    for tb in tables:
        if {singular(w) for w in tb.name.split("_")} <= _sing_words(q):
            cols = [c.name.replace("_", " ") for c in tb.columns if c.is_supported and not c.is_pii][:10]
            hidden = [c.name.replace("_", " ") for c in tb.columns if c.is_pii]
            text = f"{humanize(tb.name)}: {tb.description or 'no description in BigQuery'}. It has {tb.row_count or 'an unknown number of'} rows."
            text += f"\nUseful columns: {', '.join(cols)}."
            if hidden:
                text += f"\nPersonal data ({', '.join(hidden)}) is kept hidden."
            return text
    lines = [f"• {humanize(t.name)}: {t.description or 'no description'}" for t in tables]
    return "This session uses these tables:\n" + "\n".join(lines) + "\nAsk about any of them, or tell me what you'd like to see."
