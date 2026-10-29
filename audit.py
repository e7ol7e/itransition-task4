"""Human-readable evidence from the exact same scoped frames as the dashboard."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import pandas as pd
from analysis import IDENTITY_COLUMNS, parse_price
from data_tools import DashboardState, StoreTools, StrictModel, records
from pydantic import Field


class AuditRequest(StrictModel):
    state: DashboardState = Field(default_factory=DashboardState)
    kind: Literal["identity", "person", "revenue", "copies", "day", "buyer", "authors"]
    user_id: int | None = None
    day: date | None = None
    aliases_only: bool = True
    search: str = Field(default="", max_length=100)
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=25, ge=1, le=100)


def page(frame, args):
    return {
        "rows": records(frame.iloc[args.offset : args.offset + args.limit]),
        "total_rows": len(frame),
        "offset": args.offset,
        "limit": args.limit,
        "has_more": args.offset + args.limit < len(frame),
    }


def person_id(data, state, user_id):
    if user_id is None:
        raise ValueError("Choose a user record ID")
    match = data.tables[state.store]["users"].loc[lambda f: f["id"] == user_id]
    if match.empty:
        raise ValueError("Unknown user ID in this store")
    person = int(match.iloc[0]["person_id"])
    if person not in set(data.scope(state)["users"]["person_id"]):
        raise ValueError("This person is outside the current dashboard filters")
    return person


def explain(data: StoreTools, args: AuditRequest):
    state = args.state
    scope = data.scope(state)
    orders, users, books = scope["orders"], scope["users"], scope["books"]
    directory = data.tables[state.store]["users"]
    base = {
        "kind": args.kind,
        "state": state.model_dump(),
        "scope_label": "Current dashboard filters" if state.filters else "Whole store",
        "sources": ["users.csv", "orders.parquet", "books.yaml"],
    }
    if args.kind == "identity":
        ids = users["person_id"].unique()
        groups = directory.loc[directory["person_id"].isin(ids)].groupby(
            "person_id", sort=True
        )
        frame = groups.agg(
            record_count=("id", "size"), name=("name", "first")
        ).reset_index()
        frame["ids"] = frame["person_id"].map(
            groups["id"].agg(lambda v: sorted(int(x) for x in v))
        )
        stats = {
            "matching_records": len(users),
            "unique_people": len(ids),
            "aliases_in_scope": len(users) - len(ids),
            "linked_groups": int((frame.record_count > 1).sum()),
        }
        if args.aliases_only:
            frame = frame.loc[frame.record_count > 1]
        if args.search:
            text = args.search.casefold()
            frame = frame.loc[
                frame["name"].fillna("").str.casefold().str.contains(text, regex=False)
                | frame["ids"].map(lambda values: any(text in str(x) for x in values))
            ]
        frame = frame.sort_values(
            ["record_count", "person_id"], ascending=[False, True], kind="stable"
        )
        return {
            **base,
            "title": "From records to real people",
            "stats": stats,
            **page(frame, args),
            "method": "Connect records sharing any three known normalized personal fields. Connected chains represent one person under the assignment’s one-field-change assumption. Missing values never count as a match.",
            "note": "Group details include every alias in the store directory, even if an alias is outside the current filter.",
        }
    if args.kind == "person":
        person = person_id(data, state, args.user_id)
        people = directory.loc[directory["person_id"] == person].sort_values("id")
        links = data.results[state.store]["links"]
        links = links.loc[
            links["id_a"].isin(people["id"]) & links["id_b"].isin(people["id"])
        ]
        by_id = people.set_index("id")
        edges = []
        for link in links.itertuples():
            left, right = by_id.loc[link.id_a], by_id.loc[link.id_b]
            matching = []
            different = []
            unknown = []
            for field in IDENTITY_COLUMNS:
                a, b = left[field], right[field]
                if pd.isna(a) or pd.isna(b):
                    unknown.append(field)
                elif str(a).casefold() == str(b).casefold():
                    matching.append(field)
                else:
                    different.append(field)
            edges.append(
                {
                    "id_a": int(link.id_a),
                    "id_b": int(link.id_b),
                    "matched_fields": matching,
                    "different_fields": different,
                    "unknown_fields": unknown,
                }
            )
        scoped = orders.loc[orders["person_id"] == person]
        lifetime = data.tables[state.store]["orders"].loc[
            lambda f: f["person_id"] == person
        ]
        return {
            **base,
            "title": "Why these records belong together",
            "person_id": person,
            "records": records(people[["id", *IDENTITY_COLUMNS]]),
            "edges": edges,
            "scoped_orders": len(scoped),
            "scoped_spending_cents": int(scoped["paid_price_cents"].sum()),
            "lifetime_spending_cents": int(lifetime["paid_price_cents"].sum()),
            "method": "Edges show the minimal linking evidence saved during reconciliation, not every possible match. A chain can connect records whose endpoints do not directly share three fields.",
            "normalization": "NFKC, trimmed/collapsed whitespace and case-insensitive comparison; phone punctuation removed. Missing values remain unknown. These are normalized records.",
        }
    if args.kind == "authors":
        catalog = (
            books.loc[books["author_set"] != ""]
            .groupby("author_set")
            .size()
            .rename("catalog_titles")
        )
        sold = (
            orders.loc[orders["author_set"] != ""]
            .groupby("author_set")["quantity"]
            .sum()
            .rename("copies_sold")
        )
        frame = (
            catalog.to_frame()
            .join(sold, how="left")
            .fillna({"copies_sold": 0})
            .reset_index()
        )
        frame["copies_sold"] = frame["copies_sold"].astype("int64")
        frame = frame.sort_values(
            ["copies_sold", "author_set"], ascending=[False, True], kind="stable"
        )
        return {
            **base,
            "title": "Author sets, counted once",
            "unique_author_sets": len(frame),
            **page(frame, args),
            "method": "Names are normalized into unordered sets. A solo author and a co-author pair are different sets. Catalog counts include unsold titles; popularity sums quantity, not order rows.",
        }
    if args.kind == "day":
        if args.day is None:
            raise ValueError("Choose a calendar date")
        orders = orders.loc[orders["date"] == args.day.isoformat()]
    if args.kind == "buyer":
        person = person_id(data, state, args.user_id)
        orders = orders.loc[orders["person_id"] == person]
        base["ids"] = sorted(
            int(x) for x in directory.loc[directory.person_id == person, "id"]
        )
    frame = orders.sort_values(["date", "id"], kind="stable")
    result = page(
        frame[
            [
                "id",
                "date",
                "user_id",
                "person_id",
                "book_id",
                "title",
                "quantity",
                "raw_unit_price",
                "currency",
                "paid_price_cents",
            ]
        ],
        args,
    )
    for row in result["rows"]:
        amount, currency = parse_price(row["raw_unit_price"])
        rate = Decimal("1.2") if currency == "EUR" else Decimal(1)
        unrounded = amount * rate * int(row["quantity"])
        cents = int(unrounded.quantize(Decimal(".01"), rounding=ROUND_HALF_UP) * 100)
        if cents != row["paid_price_cents"]:
            raise ValueError("Source-price reconciliation failed")
        row.update(
            source_amount=str(amount),
            exchange_rate=str(rate),
            unrounded_usd=str(unrounded),
        )
    return {
        **base,
        "title": {
            "revenue": "Every cent, accounted for",
            "copies": "Orders and copies are different",
            "day": f"Revenue on {args.day}",
            "buyer": "Purchases across all aliases",
        }[args.kind],
        **result,
        "order_count": len(frame),
        "copies": int(frame["quantity"].sum()),
        "revenue_cents": int(frame["paid_price_cents"].sum()),
        "method": "For each order: quantity × original unit price × FX rate, then round the complete line once to cents (half up). Sum integer cents across every matching order, not just this page. EUR 1 = USD 1.20.",
    }
