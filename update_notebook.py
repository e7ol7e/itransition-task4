"""Append the Part 1 learning walkthrough without replacing the user's early cells.

Function cells are copied from analysis.py so the notebook exposes the actual
implementation. Re-run this script after changing those functions to refresh them.
"""

import inspect
import json
from pathlib import Path

import analysis

ROOT = Path(__file__).resolve().parent
path = ROOT / "explore.ipynb"
notebook = json.loads(path.read_text(encoding="utf-8"))
notebook["cells"] = [cell for cell in notebook["cells"] if not cell.get("id", "").startswith("part1-")]
# Keep the earlier lesson portable when opened in another working directory.
for cell in notebook["cells"]:
    if cell.get("id") == "c3e433e8":
        cell["source"] = ('from pathlib import Path\nimport pandas as pd\n\n'
                          'task_dir = Path.cwd() if (Path.cwd() / "data" / "STORE1").exists() else Path.cwd() / "task4"\n'
                          'store_dir = task_dir / "data" / "STORE1"\n'
                          'users = pd.read_csv(store_dir / "users.csv")\nusers.head()').splitlines(keepends=True)

counter = 0


def add(kind, text):
    global counter
    counter += 1
    cell = {"cell_type": kind, "id": f"part1-{counter:02}", "metadata": {}, "source": text.strip().splitlines(keepends=True)}
    if kind == "code":
        cell.update(execution_count=None, outputs=[])
    notebook["cells"].append(cell)


def step(title, explanation, code):
    add("markdown", f"## {title}\n\n{explanation}")
    add("code", code)


def source(*names):
    return "\n\n".join(inspect.getsource(getattr(analysis, name)) for name in names)


step("1. Load the three related tables", "A store has a user directory, a book catalog and order lines. An order's `user_id` refers to a user; `book_id` refers to a book. CSV, Parquet and YAML need different readers, but all become DataFrames. YAML keys such as `:id` will be cleaned later.\n\nRun the notebook from top to bottom. The original source files remain unchanged.", '''
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
import sys
import yaml

sys.path.insert(0, str(task_dir.resolve()))
import analysis
from analysis import CENT, IDENTITY_COLUMNS, TASK_DIR

raw_users = pd.read_csv(store_dir / "users.csv")
raw_orders = pd.read_parquet(store_dir / "orders.parquet")
raw_books = pd.DataFrame(yaml.safe_load((store_dir / "books.yaml").read_text(encoding="utf-8")))
display(pd.DataFrame({"table": ["users", "orders", "books"],
                      "rows": [len(raw_users), len(raw_orders), len(raw_books)]}))
display(raw_orders.head(3))
display(raw_books.head(3))
''')
step("2. Normalize text and protect record IDs", "Whitespace and phone punctuation must not make the same details look different. `clean_text` trims and collapses whitespace, normalizes Unicode and converts explicit missing markers to `pd.NA`. We lowercase email addresses and remove non-digit characters from phones. Names and addresses retain their display spelling; comparison keys will be case-insensitive.\n\n`check_ids` removes exact duplicate rows, then rejects conflicting records sharing one ID. Silently keeping the first conflicting ID could attach sales to the wrong customer. Missing addresses remain unknown; we do not invent them or drop the users.", source("clean_text", "check_ids", "clean_users") + '''
users_clean = clean_users(raw_users)
display(users_clean.head())
display(users_clean.isna().sum().rename("missing_values"))
''')
step("3. Generalize our three-field matching", "Previously we checked address + phone + email, which finds changed names. To allow **any one field** to change, consider all four possible groups of three fields. `combinations(columns, 3)` creates these groups. `.notna().all(axis=1)` requires actual evidence in every compared field.\n\nThis cell previews direct matches. It does not delete aliases: their IDs are still needed to connect orders.", '''
comparison_keys = users_clean[IDENTITY_COLUMNS].apply(lambda column: column.str.casefold())
candidate_counts = []
for columns in combinations(IDENTITY_COLUMNS, 3):
    known = comparison_keys[list(columns)].notna().all(axis=1)
    matching = comparison_keys.loc[known].duplicated(subset=list(columns), keep=False)
    candidate_counts.append({"matching_fields": ", ".join(columns), "participating_records": int(matching.sum())})
pd.DataFrame(candidate_counts)
''')
step("4. Link aliases, including chains", "If A matches B on three fields, and B matches C on three fields, all three belong to the same connected group under our reconciliation rule. A and C need not match directly.\n\nThe dictionary `seen` finds earlier records with the same three-field signature. `parent` stores the groups. `find` follows a record's group links to its representative; joining representatives merges the groups. This is called **union-find**. The smallest ID is a deterministic label, not a claim that this is the customer's original account. Missing values never count as a shared detail.\n\nThe returned mapping has one row per source user ID. `links` records the evidence used for each merge, so the decision can be inspected.", source("reconcile_users") + '''
user_mapping, identity_links = reconcile_users(users_clean)
print("Original records:", len(users_clean))
print("Real unique users:", user_mapping["person_id"].nunique())
display(identity_links.head(10))
''')
step("5. Clean the catalog and represent author sets", "The YAML keys have a leading colon: `removeprefix` removes it. Invalid or missing publication years become nullable `Int64` values; those books remain usable for revenue and author analysis. A tuple of sorted, distinct author names represents a set: `John, Paul` and `Paul, John` share one key, while `John` alone has another. We preserve the original author text for display.\n\nHere commas separate author names in the supplied catalog. Unknown authors form an empty tuple and are reported separately rather than counted as a named author set.", source("author_set", "clean_books") + '''
books_clean = clean_books(raw_books)
display(books_clean.head(5))
print("Unique known author sets:", len({key for key in books_clean["author_set"] if key}))
print("Unknown publication years:", books_clean["year"].isna().sum())
''')
step("6. Read money without losing cents", "`Decimal` represents decimal money exactly. Binary floats can introduce tiny errors. The source uses USD/EUR prefixes, suffixes, symbols, and special cent notation: `$70¢50` and `70$50¢` both mean USD 70.50. Our regex patterns consume the entire value; unknown formats raise an error instead of being silently changed.\n\n`parse_price` returns the amount and its source currency. Conversion happens once in the order-cleaning step.", 'import re\n\n' + source("parse_price") + '''
for example in ["$70.50", "70 USD", "EUR35.", "€38¢50", "70$50¢"]:
    print(example, "->", parse_price(example))
''')
step("7. Parse mixed timestamps and extract dates", "These files mix ISO timestamps, named months and US-style slash dates. Unambiguous values with the second slash component greater than 12 establish `MM/DD/YY` for slash dates. We normalize AM/PM punctuation and separators before `pd.to_datetime(format='mixed')`. Invalid values become `NaT` so we can identify them. No timezone is supplied; we retain source local dates.\n\n`.dt.normalize()` sets the time to midnight, allowing all sales on the same calendar day to share a grouping key.", source("parse_timestamps") + '''
timestamps = parse_timestamps(raw_orders["timestamp"])
display(pd.DataFrame({"source": raw_orders["timestamp"], "parsed": timestamps,
                      "date": timestamps.dt.normalize()}).head(8))
print("Unparsed timestamps:", timestamps.isna().sum())
''')
step("8. Calculate paid_price and quarantine invalid orders", "For each valid line: `paid_price = quantity × unit_price × exchange_rate`. We apply EUR → USD at exactly 1.2 and round the **completed line** to cents using `ROUND_HALF_UP`. For example, EUR 0.99 × 3 × 1.2 = USD 3.564 → **3.56**, not 3.57 from prematurely rounding the unit price.\n\n`paid_price` is Decimal USD. `paid_price_cents` is an integer used for exact summation and dashboard transport. Unknown shipping remains missing. Invalid quantities, prices or dates go into a separate rejected table with an explanation; they cannot silently disappear into a groupby result.", source("clean_orders") + '''
orders_clean, rejected_orders = clean_orders(raw_orders)
display(orders_clean[["id", "quantity", "raw_unit_price", "currency", "unit_price", "paid_price", "date"]].head(10))
print("Valid orders:", len(orders_clean), "Rejected:", len(rejected_orders))
display(rejected_orders.head())
''')
step("9. Join each order to its person and author set", "`merge` connects tables using keys, like a SQL join. We match order `user_id` to the identity mapping and order `book_id` to the catalog. `how='left'` retains every order. `validate='many_to_one'` checks that each lookup key has at most one matching row, preventing accidental multiplication of revenue.\n\nThe assertions check every order found its user and book, and the joins preserved the row count.", '''
orders_enriched = orders_clean.merge(user_mapping, on="user_id", how="left", validate="many_to_one")
book_lookup = books_clean[["id", "author_set"]].rename(columns={"id": "book_id"})
orders_enriched = orders_enriched.merge(book_lookup, on="book_id", how="left", validate="many_to_one")
assert len(orders_enriched) == len(orders_clean)
assert orders_enriched["person_id"].notna().all()
assert orders_enriched["author_set"].notna().all()
display(orders_enriched[["id", "user_id", "person_id", "book_id", "author_set", "paid_price"]].head())
''')
step("10. Group sales by day and find the top five", "`groupby('date')` partitions the rows into daily groups; selecting `paid_price_cents` chooses the values, and `.sum()` reduces each group to one total. Sorting revenue descending puts the strongest days first. Dates ascending provide a deterministic tie-break.\n\nWe add missing calendar dates with zero revenue for a continuous daily chart. This does not invent orders. Use `strftime('%Y-%m-%d')` for the required display format.", '''
daily_revenue = orders_enriched.groupby("date")["paid_price_cents"].sum().sort_index()
calendar = pd.date_range(daily_revenue.index.min(), daily_revenue.index.max(), freq="D")
daily_revenue = daily_revenue.reindex(calendar, fill_value=0).rename_axis("date").reset_index()
daily_revenue["revenue_usd"] = daily_revenue["paid_price_cents"].map(lambda value: Decimal(int(value)) / 100)
top_five = daily_revenue.sort_values(["paid_price_cents", "date"], ascending=[False, True]).head(5)
display(top_five.assign(date=top_five["date"].dt.strftime("%Y-%m-%d"))[["date", "revenue_usd"]])
assert daily_revenue["paid_price_cents"].sum() == orders_enriched["paid_price_cents"].sum()
''')
step("11. Plot the daily revenue", "Matplotlib takes the date column for the horizontal axis and revenue for the vertical axis. We convert cents to floating-point dollars **only for plotting**, after exact financial calculations are finished. The saved PNG can be inspected separately from the notebook.", '''
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter

fig, ax = plt.subplots(figsize=(12, 4))
ax.plot(daily_revenue["date"], daily_revenue["paid_price_cents"].astype(float) / 100,
        color="#166c50", linewidth=1)
ax.set(title="STORE1 · Daily revenue", xlabel="Date", ylabel="Revenue (USD)")
ax.yaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
ax.grid(axis="y", alpha=0.2)
fig.autofmt_xdate()
fig.tight_layout()
(task_dir / "output").mkdir(exist_ok=True)
fig.savefig(task_dir / "output" / "store1-daily-revenue.png", dpi=150)
plt.show()
''')
step("12. Rank author sets by copies sold", "We sum `quantity`, not the number of order rows: an order for five copies contributes five sales. Co-authored books contribute to their shared set rather than separately inflating each author's total. The most popular set is the group with the largest quantity sum. A tie returns every winning set.", '''
author_sales = orders_enriched.groupby("author_set")["quantity"].sum().sort_values(ascending=False)
author_sales = author_sales.loc[[bool(key) for key in author_sales.index]]
most_popular = author_sales.loc[author_sales == author_sales.max()]
display(author_sales.head(10).rename("copies_sold").reset_index())
print("Winning author set(s):")
display(most_popular.rename("copies_sold").reset_index())
''')
step("13. Find the best buyer across all aliases", "Grouping by `person_id` combines spending from all reconciled IDs. Grouping only by `user_id` would split one person's purchases across aliases. After finding the maximum total, select **all directory records** for each winner, including aliases that did not themselves place an order. All tied top customers are retained.", '''
customer_spending = orders_enriched.groupby("person_id")["paid_price_cents"].sum()
best_people = customer_spending.loc[customer_spending == customer_spending.max()]
linked_users = users_clean.merge(user_mapping, left_on="id", right_on="user_id", validate="one_to_one")
for person_id, cents in best_people.items():
    aliases = linked_users.loc[linked_users["person_id"] == person_id]
    print("Best buyer IDs:", sorted(aliases["id"].tolist()))
    print("Names / aliases:", sorted(aliases["name"].dropna().unique().tolist()))
    print("Total USD:", Decimal(int(cents)) / 100)
assert customer_spending.sum() == daily_revenue["paid_price_cents"].sum()
''')
step("14. Repeat independently for all five stores", "The reusable implementation in `analysis.py` contains the same cleaning and aggregation functions shown above. `analyze_store` runs the pipeline for one folder and returns both intermediate tables and final results. We never merge users across stores: their IDs and identity assumptions are local.\n\nThis table summarizes all five stores. The next cell displays the full six requested results for each one.", '''
results = [analysis.analyze_store(folder) for folder in sorted((task_dir / "data").glob("STORE*"))]
display(pd.DataFrame([{
    "store": result["summary"]["store"],
    "orders": result["summary"]["orders"],
    "unique_users": result["summary"]["unique_users"],
    "unique_author_sets": result["summary"]["unique_author_sets"],
    "copies_sold": result["summary"]["units_sold"],
    "total_revenue_usd": Decimal(result["summary"]["revenue_cents"]) / 100,
    "rejected_orders": result["summary"]["quality"]["rejected_orders"],
} for result in results]))
''')
step("15. Inspect every store's final answers", "Each store gets its own top-five table, unique-user count, author-set count, popular author set(s), best-buyer ID array(s), and daily chart. Catalog and directory counts cover all records; sales rankings cover orders. Monetary totals reconcile to the same underlying order lines.", '''
from IPython.display import Markdown, display

for result in results:
    summary = result["summary"]
    display(Markdown("### " + summary["store"]))
    print("Real unique users:", summary["unique_users"])
    print("Unique author sets:", summary["unique_author_sets"])
    print("Most popular:", summary["popular_authors"], "Copies per winning set:", summary["popular_units"])
    for buyer in summary["best_buyers"]:
        print("Best buyer IDs:", buyer["ids"], "Aliases:", buyer["names"],
              "Spent USD:", Decimal(buyer["spending_cents"]) / 100)
    display(pd.DataFrame(summary["top_days"]).assign(
        revenue_usd=lambda frame: frame["revenue_cents"].map(lambda cents: Decimal(cents) / 100)
    )[["date", "revenue_usd"]])
    fig, ax = plt.subplots(figsize=(11, 3))
    ax.plot(result["daily"]["date"], result["daily"]["paid_price_cents"].astype(float) / 100,
            color="#166c50", linewidth=1)
    ax.set(title=summary["store"] + " · Daily revenue", ylabel="USD")
    ax.yaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
    ax.grid(axis="y", alpha=0.2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(task_dir / "output" / (summary["store"].lower() + "-daily-revenue.png"), dpi=150)
    plt.show()
''')
step("16. Build the browser dashboard and audit exports", "`build_all` writes cleaned tables, identity mapping, merge evidence and rejected rows to local `output/STORE*/` CSV files. The dashboard embeds only store aggregates and the required top-buyer IDs/names; it does not publish the full contact directory. `summary.json` contains every calculated result and the calculation rules.\n\nThe generated page has five store views, revenue tooltips, chart range controls and a JSON export button. It works without a CDN or external chart service. Re-run this cell after changing source data or analysis code, then rebuild the Docker image for deployment. Part 2's AI question-answering is a separate next stage.", '''
exported_results = analysis.build_all()
print("Dashboard:", task_dir / "dashboard" / "dist" / "index.html")
print("All calculated values:", task_dir / "output" / "summary.json")
''')
path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"Preserved earlier lessons and added {counter // 2} explained steps ({len(notebook['cells'])} cells total).")
