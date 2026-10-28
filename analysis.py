"""Task 4, part 1: reproducible cleaning, reconciliation and store metrics.

Run with this task's Python environment: python analysis.py
All money is Decimal until each order line is rounded to integer USD cents.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from pathlib import Path

import pandas as pd
import yaml

TASK_DIR = Path(__file__).resolve().parent
IDENTITY_COLUMNS = ["name", "address", "phone", "email"]
CENT = Decimal("0.01")


def clean_text(series):
    """Normalize formatting, preserving missing values rather than inventing data."""
    result = series.astype("string").str.normalize("NFKC").str.strip()
    result = result.str.replace(r"\s+", " ", regex=True)
    return result.mask(result.str.casefold().isin(["", "null", "none", "nan", "n/a", "-"]))


def check_ids(frame, label):
    """Remove exact copies; reject conflicting records with the same primary key."""
    frame = frame.drop_duplicates().copy()
    ids = pd.to_numeric(frame["id"], errors="raise")
    if ids.isna().any() or (ids % 1 != 0).any() or ids.duplicated().any():
        raise ValueError(f"{label}: missing, noninteger or conflicting IDs")
    frame["id"] = ids.astype("int64")
    return frame


def clean_users(raw):
    users = check_ids(raw, "users")
    for column in IDENTITY_COLUMNS:
        users[column] = clean_text(users[column])
    users["phone"] = users["phone"].str.replace(r"\D", "", regex=True).replace("", pd.NA)
    users["email"] = users["email"].str.casefold()
    return users


def reconcile_users(users):
    """Connect records sharing any three known personal fields, including chains.

    An address missing from both records is NOT evidence of a shared identity.
    The smallest ID labels a connected component; every alias is retained.
    """
    keys = users[IDENTITY_COLUMNS].apply(lambda column: column.str.casefold())
    parent = {int(value): int(value) for value in users["id"]}

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    links = []
    for columns in combinations(IDENTITY_COLUMNS, 3):
        seen = {}
        for user_id, values in zip(users["id"], keys[list(columns)].itertuples(index=False, name=None)):
            if any(pd.isna(value) for value in values):
                continue
            user_id = int(user_id)
            if values in seen:
                previous = seen[values]
                left, right = find(previous), find(user_id)
                if left != right:
                    parent[max(left, right)] = min(left, right)
                    links.append({"id_a": previous, "id_b": user_id, "matched_fields": ", ".join(columns)})
            else:
                seen[values] = user_id
    mapping = users[["id"]].rename(columns={"id": "user_id"}).copy()
    mapping["person_id"] = mapping["user_id"].map(lambda value: find(int(value)))
    return mapping, pd.DataFrame(links, columns=["id_a", "id_b", "matched_fields"])


def author_set(value):
    """Order and repeated names do not change a set. Missing authors stay unknown."""
    if pd.isna(value):
        return ()
    return tuple(sorted({name.strip().casefold() for name in value.split(",") if name.strip()}))


def clean_books(raw):
    books = raw.rename(columns=lambda column: column.removeprefix(":"))
    books = check_ids(books, "books")
    for column in ["title", "author", "genre", "publisher"]:
        books[column] = clean_text(books[column])
    year = pd.to_numeric(books["year"], errors="coerce")
    books["year"] = year.where((year > 0) & (year <= 9999) & (year % 1 == 0)).astype("Int64")
    books["author_set"] = books["author"].map(author_set)
    return books


def parse_price(value):
    """Read the source's prefix/suffix currencies and dollar/euro + cent notation."""
    value = str(value).strip().upper().replace(" ", "")
    currency_pattern = r"(USD|EUR|\$|\u20ac)"
    number = r"(\d+(?:\.\d{0,2})?)"
    cents = r"(\d{1,2})"
    # Examples: $70¢50 and 70$50¢ mean 70 dollars and 50 cents.
    match = re.fullmatch(currency_pattern + r"(\d+)\u00a2" + cents, value)
    if match:
        symbol, whole, fraction = match.groups()
        amount = Decimal(whole) + Decimal(fraction) / 100
    else:
        match = re.fullmatch(r"(\d+)" + currency_pattern + cents + r"\u00a2", value)
        if match:
            whole, symbol, fraction = match.groups()
            amount = Decimal(whole) + Decimal(fraction) / 100
        else:
            prefix = re.fullmatch(currency_pattern + number, value)
            suffix = re.fullmatch(number + currency_pattern, value)
            if prefix:
                symbol, amount = prefix.groups()
            elif suffix:
                amount, symbol = suffix.groups()
            else:
                raise ValueError(f"Unrecognized price: {value!r}")
            amount = Decimal(amount)
    return amount, "EUR" if symbol in ("EUR", "\u20ac") else "USD"


def parse_timestamps(series):
    """Slash dates are MM/DD/YY; the data contains unambiguous US-style examples."""
    text = series.astype("string").str.replace(r"(?i)([ap])\.?m\.?", r"\1m", regex=True)
    text = text.str.replace(r"[,;]", " ", regex=True).str.strip()
    return pd.to_datetime(text, format="mixed", errors="coerce", dayfirst=False)


def clean_orders(raw):
    orders = check_ids(raw, "orders")
    orders["raw_timestamp"] = orders["timestamp"]
    orders["timestamp"] = parse_timestamps(orders["timestamp"])
    orders["date"] = orders["timestamp"].dt.normalize()
    orders["shipping"] = clean_text(orders["shipping"])
    orders["raw_unit_price"] = orders["unit_price"]
    amounts, currencies, paid_cents, reasons = [], [], [], []
    for row in orders.itertuples(index=False):
        try:
            quantity = Decimal(str(row.quantity))
            if not quantity.is_finite() or quantity <= 0 or quantity % 1:
                raise ValueError("quantity must be a positive integer")
            amount, currency = parse_price(row.unit_price)
            usd = amount * (Decimal("1.2") if currency == "EUR" else Decimal(1))
            cents = int((usd * quantity).quantize(CENT, rounding=ROUND_HALF_UP) * 100)
            if pd.isna(row.timestamp):
                raise ValueError("unrecognized timestamp")
            amounts.append(usd)
            currencies.append(currency)
            paid_cents.append(cents)
            reasons.append("")
        except (ValueError, ArithmeticError) as error:
            amounts.append(None)
            currencies.append(None)
            paid_cents.append(None)
            reasons.append(str(error))
    orders["unit_price"] = amounts  # Exact USD value; round the completed line, not the unit.
    orders["currency"] = currencies
    orders["paid_price_cents"] = pd.array(paid_cents, dtype="Int64")
    orders["rejection_reason"] = reasons
    rejected = orders.loc[orders["rejection_reason"] != ""].copy()
    orders = orders.loc[orders["rejection_reason"] == ""].copy()
    orders["quantity"] = orders["quantity"].astype("int64")
    orders["paid_price"] = orders["paid_price_cents"].map(lambda cents: Decimal(int(cents)) / 100)
    return orders.drop(columns="rejection_reason"), rejected


def load_store(store_dir):
    """Keep the five stores separate; local IDs are not global identifiers."""
    return (
        pd.read_csv(store_dir / "users.csv"),
        pd.read_parquet(store_dir / "orders.parquet"),
        pd.DataFrame(yaml.safe_load((store_dir / "books.yaml").read_text(encoding="utf-8"))),
    )


def analyze_store(store_dir):
    raw_users, raw_orders, raw_books = load_store(store_dir)
    users = clean_users(raw_users)
    books = clean_books(raw_books)
    orders, rejected = clean_orders(raw_orders)
    mapping, links = reconcile_users(users)
    orders = orders.merge(mapping, on="user_id", how="left", validate="many_to_one")
    orders = orders.merge(books[["id", "author_set"]].rename(columns={"id": "book_id"}),
                          on="book_id", how="left", validate="many_to_one")
    if orders["person_id"].isna().any() or orders["author_set"].isna().any():
        raise ValueError(f"{store_dir.name}: orders reference unknown users or books")

    daily = orders.groupby("date")["paid_price_cents"].sum().sort_index()
    calendar = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(calendar, fill_value=0).rename_axis("date").reset_index()
    daily["revenue"] = daily["paid_price_cents"].map(lambda value: Decimal(int(value)) / 100)
    top_days = daily.sort_values(["paid_price_cents", "date"], ascending=[False, True]).head(5)
    popularity = orders.groupby("author_set")["quantity"].sum().sort_values(ascending=False)
    popularity = popularity.loc[[bool(value) for value in popularity.index]]
    spending = orders.groupby("person_id")["paid_price_cents"].sum().sort_values(ascending=False)
    winners = spending.loc[spending == spending.max()]

    display_names = {}
    for authors in books["author"].dropna():
        for author in authors.split(","):
            display_names.setdefault(author.strip().casefold(), author.strip())

    def format_authors(key):
        return [display_names[name] for name in key]

    buyers = []
    for person_id, cents in winners.items():
        aliases = users.merge(mapping, left_on="id", right_on="user_id")
        aliases = aliases.loc[aliases["person_id"] == person_id]
        buyers.append({"ids": sorted(int(value) for value in aliases["id"]),
                       "names": sorted(aliases["name"].dropna().unique().tolist()),
                       "spending_cents": int(cents)})
    summary = {
        "store": store_dir.name,
        "users_raw": len(raw_users), "unique_users": int(mapping["person_id"].nunique()),
        "alias_records": len(users) - int(mapping["person_id"].nunique()),
        "unique_author_sets": len({value for value in books["author_set"] if value}),
        "books": len(books), "orders": len(orders), "units_sold": int(orders["quantity"].sum()),
        "revenue_cents": int(orders["paid_price_cents"].sum()),
        "popular_authors": [format_authors(key) for key, count in popularity.items() if count == popularity.max()],
        "popular_units": int(popularity.max()), "best_buyers": buyers,
        "top_days": [{"date": row.date.strftime("%Y-%m-%d"), "revenue_cents": int(row.paid_price_cents)}
                     for row in top_days.itertuples()],
        "daily": [{"date": row.date.strftime("%Y-%m-%d"), "revenue_cents": int(row.paid_price_cents)}
                  for row in daily.itertuples()],
        "quality": {"missing_addresses": int(users["address"].isna().sum()),
                    "unknown_publication_years": int(books["year"].isna().sum()),
                    "missing_publishers": int(books["publisher"].isna().sum()),
                    "unknown_author_books": int(books["author_set"].map(lambda value: not value).sum()),
                    "exact_duplicate_rows_removed": sum(len(raw) - len(raw.drop_duplicates())
                                                        for raw in (raw_users, raw_orders, raw_books)),
                    "rejected_orders": len(rejected)},
    }
    assert int(daily["paid_price_cents"].sum()) == int(spending.sum()) == summary["revenue_cents"]
    assert len(orders) + len(rejected) == len(raw_orders.drop_duplicates())
    assert int(orders["quantity"].sum()) == int(orders.groupby("author_set")["quantity"].sum().sum())
    return {"users": users, "books": books, "orders": orders, "mapping": mapping,
            "links": links, "rejected": rejected, "daily": daily,
            "top_days": top_days, "popularity": popularity, "spending": spending, "summary": summary}


def build_all():
    """Export aggregate-only public data and detailed local audit CSVs."""
    results = [analyze_store(path) for path in sorted((TASK_DIR / "data").glob("STORE*"))]
    if len(results) != 5:
        raise ValueError("Expected the five store folders")
    output = TASK_DIR / "output"
    output.mkdir(exist_ok=True)
    for result in results:
        folder = output / result["summary"]["store"]
        folder.mkdir(exist_ok=True)
        for key in ["users", "books", "orders", "mapping", "links", "rejected", "daily", "top_days"]:
            result[key].to_csv(folder / f"{key}.csv", index=False)
    payload = {"stores": [result["summary"] for result in results], "methodology": {
        "currency": "USD; EUR 1 = USD 1.20. Round each complete order line to cents (half up).",
        "identity": "Within each store, connect records sharing any 3 known normalized personal fields; retain transitive aliases.",
        "authors": "Distinct unordered author sets across the catalog; popularity counts copies sold by each set.",
        "dates": "Local source timestamps; slash dates use MM/DD/YY. Missing calendar days have zero revenue.",
        "ties": "Top days break ties by earlier date. All tied popular author sets and top buyers are included.",
    }}
    (output / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    dist = TASK_DIR / "dashboard" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    template = (TASK_DIR / "dashboard" / "index.template.html").read_text(encoding="utf-8")
    embedded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    (dist / "index.html").write_text(template.replace("__DASHBOARD_DATA__", embedded), encoding="utf-8")
    for asset in ["app.js", "app.css"]:
        (dist / asset).write_text((TASK_DIR / "dashboard" / asset).read_text(encoding="utf-8"), encoding="utf-8")
    print(pd.DataFrame([{key: result["summary"][key] for key in
                        ["store", "orders", "unique_users", "unique_author_sets", "revenue_cents"]}
                       for result in results]).to_string(index=False))
    return results


if __name__ == "__main__":
    build_all()
