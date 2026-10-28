"""Validated pandas operations shared by HTTP, chat tools, and dashboard controls.

The model supplies a query description, never Python, pandas expressions, or SQL.
All joins, filtering, counting, money arithmetic and ordering happen here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from analysis import analyze_store

ROOT = Path(__file__).resolve().parent
Dataset = Literal["orders", "books", "users"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(StrictModel):
    field: str = Field(max_length=60)
    op: Literal["eq", "ne", "contains", "starts_with", "starts_with_any", "in", "not_in",
                "gt", "gte", "lt", "lte", "between", "is_missing"]
    value: Any = None


class Aggregate(StrictModel):
    op: Literal["count", "sum", "avg", "min", "max", "nunique"]
    field: str | None = None


class Query(StrictModel):
    store: Literal["STORE1", "STORE2", "STORE3", "STORE4", "STORE5"]
    dataset: Dataset
    filters: list[Filter] = Field(default_factory=list, max_length=12)
    columns: list[str] = Field(default_factory=list, max_length=12)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    aggregates: list[Aggregate] = Field(default_factory=list, max_length=8)
    sort_by: str | None = None
    descending: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class DashboardState(StrictModel):
    store: Literal["STORE1", "STORE2", "STORE3", "STORE4", "STORE5"] = "STORE1"
    view: Literal["overview", "books", "users", "orders"] = "overview"
    filter_dataset: Dataset = "orders"
    filters: list[Filter] = Field(default_factory=list, max_length=12)
    chart_range: Literal["all", "30", "90"] = "all"


def records(frame: pd.DataFrame):
    # pandas' JSON conversion handles nullable ints, timestamps, numpy scalars.
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def validate_fields(frame, fields):
    unknown = set(fields) - set(frame.columns)
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}. Available: {', '.join(frame.columns)}")


def filtered(frame: pd.DataFrame, filters: list[Filter]):
    result = frame
    for condition in filters:
        validate_fields(result, [condition.field])
        series, value, op = result[condition.field], condition.value, condition.op
        if isinstance(value, (dict, list)) and op not in {"in", "not_in", "between", "starts_with_any"}:
            raise ValueError(f"{op} requires a scalar value")
        if op in {"in", "not_in", "between", "starts_with_any"}:
            if not isinstance(value, list) or len(value) > 2000:
                raise ValueError(f"{op} requires an array of at most 2000 values")
        if op == "is_missing":
            mask = series.isna() if value is not False else series.notna()
        elif op in {"contains", "starts_with", "starts_with_any"}:
            text = series.astype("string").str.casefold()
            if op == "contains":
                mask = text.str.contains(str(value).casefold(), regex=False, na=False)
            elif op == "starts_with":
                mask = text.str.startswith(str(value).casefold(), na=False)
            else:
                if not all(isinstance(item, str) for item in value):
                    raise ValueError("starts_with_any expects strings")
                mask = text.str.startswith(tuple(item.casefold() for item in value), na=False)
        elif op in {"in", "not_in"}:
            if pd.api.types.is_numeric_dtype(series):
                mask = series.isin(value)
            else:
                mask = series.astype("string").str.casefold().isin([str(item).casefold() for item in value])
            if op == "not_in":
                mask = ~mask
        elif op == "between":
            if len(value) != 2:
                raise ValueError("between expects exactly two inclusive bounds")
            mask = series.between(value[0], value[1], inclusive="both")
        elif op in {"eq", "ne"}:
            left, right = (series.astype("string").str.casefold(), str(value).casefold()) if isinstance(value, str) else (series, value)
            mask = left.eq(right) if op == "eq" else left.ne(right)
        else:
            try:
                mask = {"gt": series.gt, "gte": series.ge, "lt": series.lt, "lte": series.le}[op](value)
            except TypeError as error:
                raise ValueError("Comparison value does not match the field type") from error
        result = result.loc[mask.fillna(False)]
    return result


class StoreTools:
    def __init__(self, data_dir: Path | None = None):
        self.results = {}
        self.tables = {}
        for folder in sorted((data_dir or ROOT / "data").glob("STORE*")):
            result = analyze_store(folder)
            self.results[folder.name] = result
            users = result["users"].merge(result["mapping"], left_on="id", right_on="user_id").drop(columns="user_id")
            books = result["books"].copy()
            orders = result["orders"].copy()
            for table in (books, orders):
                table["author_set"] = table["author_set"].map(lambda names: " | ".join(names))
            orders = orders.merge(books.drop(columns="author_set").rename(columns={"id": "book_id", "year": "publication_year"}),
                                  on="book_id", validate="many_to_one")
            orders = orders.merge(users[["id", "name", "email"]].rename(columns={"id": "user_id", "name": "customer_name", "email": "customer_email"}),
                                  on="user_id", validate="many_to_one")
            orders["date"] = orders["date"].dt.strftime("%Y-%m-%d")
            orders["timestamp"] = orders["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
            orders = orders.drop(columns=["paid_price", "unit_price"])
            self.tables[folder.name] = {"users": users, "books": books, "orders": orders}
        if set(self.tables) != {f"STORE{i}" for i in range(1, 6)}:
            raise ValueError("All five store input directories are required")

    def describe(self, store: str):
        return {"store": store, "tables": {
            name: {"rows": len(frame), "fields": {column: str(frame[column].dtype) for column in frame.columns}}
            for name, frame in self.tables[store].items()},
            "money": "paid_price_cents is integer USD cents. Server supplies dollar-formatted values; do not calculate money yourself.",
            "identity": "users.id is a record/alias; person_id identifies a reconciled real person. Use nunique(person_id) for real users.",
            "authors": "author_set is a normalized, unordered set; author is the source display name(s).",
            "dates": "date is YYYY-MM-DD; between bounds are inclusive. No filters means the whole store.",
            "filters": "All filter conditions are AND. starts_with_any and in provide OR within one condition."}

    def query(self, query: Query):
        source = filtered(self.tables[query.store][query.dataset], query.filters)
        validate_fields(source, query.columns + query.group_by)
        if query.group_by and not query.aggregates:
            raise ValueError("group_by requires aggregates")
        if query.aggregates:
            grouped = source.groupby(query.group_by, dropna=False, sort=True) if query.group_by else None
            series = {}
            for aggregate in query.aggregates:
                field, op = aggregate.field, aggregate.op
                if field:
                    validate_fields(source, [field])
                elif op != "count":
                    raise ValueError(f"{op} requires a field")
                if op in {"sum", "avg"} and not pd.api.types.is_numeric_dtype(source[field]):
                    raise ValueError(f"{op} requires a numeric field")
                name = f"{op}_{field}" if field else "row_count"
                operation = "mean" if op == "avg" else op
                if grouped is not None:
                    series[name] = grouped.size() if field is None else grouped[field].agg(operation)
                else:
                    series[name] = [len(source) if field is None else getattr(source[field], operation)()]
            result = pd.DataFrame(series)
            if grouped is not None:
                result = result.reset_index()
        else:
            defaults = {"users": ["id", "name", "email", "person_id"],
                        "books": ["id", "title", "author", "genre", "year"],
                        "orders": ["id", "date", "user_id", "book_id", "quantity", "paid_price_cents"]}
            result = source[query.columns or defaults[query.dataset]]
        if query.sort_by:
            validate_fields(result, [query.sort_by])
            # Stable ordering provides repeatable ties and pagination.
            result = result.sort_values(query.sort_by, ascending=not query.descending, kind="stable", na_position="last")
        total = len(result)
        page = result.iloc[query.offset:query.offset + query.limit]
        output = records(page)
        for row in output:
            for key, value in list(row.items()):
                if key.endswith("_cents") and value is not None:
                    row[key.removesuffix("_cents") + "_usd"] = f"${value / 100:,.2f}"
        return {"store": query.store, "dataset": query.dataset, "matched_rows": len(source),
                "result_rows": total, "returned_rows": len(output), "offset": query.offset,
                "truncated": query.offset + len(output) < total, "rows": output}

    def aliases(self, store: str, user_id: int):
        result = self.results[store]
        match = result["mapping"].loc[result["mapping"]["user_id"] == user_id, "person_id"]
        if match.empty:
            raise ValueError("Unknown user ID in this store")
        person = int(match.iloc[0])
        users = self.tables[store]["users"]
        identities = users.loc[users["person_id"] == person]
        total = int(result["orders"].loc[result["orders"]["person_id"] == person, "paid_price_cents"].sum())
        return {"store": store, "person_id": person, "ids": sorted(int(value) for value in identities["id"]),
                "names": identities["name"].dropna().unique().tolist(), "spending_cents": total,
                "spending_usd": f"${total / 100:,.2f}"}

    def dashboard(self, state: DashboardState):
        data = self.results[state.store]
        tables = self.tables[state.store]
        selected = filtered(tables[state.filter_dataset], state.filters)
        orders, books, users = tables["orders"], tables["books"], tables["users"]
        if state.filters:
            if state.filter_dataset == "books":
                books = selected
                orders = orders.loc[orders["book_id"].isin(books["id"])]
                users = users.loc[users["person_id"].isin(orders["person_id"])]
            elif state.filter_dataset == "users":
                users = selected
                orders = orders.loc[orders["person_id"].isin(users["person_id"])]
                books = books.loc[books["id"].isin(orders["book_id"])]
            else:
                orders = selected
                books = books.loc[books["id"].isin(orders["book_id"])]
                users = users.loc[users["person_id"].isin(orders["person_id"])]
        daily = orders.groupby("date")["paid_price_cents"].sum()
        full_dates = pd.Index([item["date"] for item in data["summary"]["daily"]], name="date")
        daily = daily.reindex(full_dates, fill_value=0)
        top = daily.sort_values(ascending=False, kind="stable").head(5)
        popularity = orders.loc[orders["author_set"] != ""].groupby("author_set")["quantity"].sum()
        spend = orders.groupby("person_id")["paid_price_cents"].sum()
        buyers = [self.aliases(state.store, int(person)) for person in spend.loc[spend == spend.max()].index] if len(spend) else []
        for buyer in buyers:
            buyer["spending_cents"] = int(spend.loc[buyer["person_id"]])
            buyer["spending_usd"] = f"${buyer['spending_cents'] / 100:,.2f}"
        summary = {**data["summary"], "unique_users": int(users["person_id"].nunique()),
                   "users_raw": len(users), "alias_records": len(users) - int(users["person_id"].nunique()),
                   "unique_author_sets": int(books.loc[books["author_set"] != "", "author_set"].nunique()),
                   "books": len(books), "orders": len(orders), "units_sold": int(orders["quantity"].sum()),
                   "revenue_cents": int(orders["paid_price_cents"].sum()), "best_buyers": buyers,
                   "popular_units": int(popularity.max()) if len(popularity) else 0,
                   "popular_authors": [name.split(" | ") for name in popularity.loc[popularity == popularity.max()].index] if len(popularity) else [],
                   "daily": [{"date": date, "revenue_cents": int(value)} for date, value in daily.items()],
                   "top_days": [{"date": date, "revenue_cents": int(value)} for date, value in top.items()],
                   "filtered": bool(state.filters)}
        if not state.filters:
            summary = {**data["summary"], "filtered": False}
        return {"state": state.model_dump(), "summary": summary,
                "table_counts": {"books": len(books), "users": len(users), "orders": len(orders)}}


def render_evidence(name: str, result: dict) -> str:
    """Generate the factual answer from actual tool output, not model arithmetic."""
    if name == "get_store_summary":
        labels = {"unique_users": "Real unique users", "unique_author_sets": "Unique author sets",
                  "orders": "Orders", "units_sold": "Copies sold", "revenue_usd": "Revenue",
                  "popular_authors": "Most popular author sets", "popular_units": "Copies sold per winning author set",
                  "top_days": "Top revenue days", "best_buyers": "Best buyers"}
        return "\n".join(f"{labels.get(key, key)}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value}"
                         for key, value in result.items() if key != "store")
    if name == "get_user_aliases":
        return f"User IDs: {result['ids']}\nNames: {', '.join(result['names'])}\nTotal spending: {result['spending_usd']}"
    if name == "update_dashboard":
        text = f"Showing {result['state']['view']} for {result['state']['store']}. Matching {result['state']['filter_dataset']}: {result['matching_rows']}."
        if result.get("classification"):
            text += "\nTitle meaning was classified by AI; the selected IDs and record count were validated by the server."
        return text
    if name == "query_data":
        rows = result["rows"]
        if not rows:
            return f"No matching results in {result['store']}."
        lines = []
        for row in rows:
            visible = {key: value for key, value in row.items() if not (key.endswith("_cents") and key.removesuffix("_cents") + "_usd" in row)}
            lines.append("; ".join(f"{key}: {value}" for key, value in visible.items()))
        if result["truncated"]:
            lines.append(f"Showing {len(rows)} of {result['result_rows']} results; {result['matched_rows']} source rows match.")
        return "\n".join(lines)
    return json.dumps(result, ensure_ascii=False)
