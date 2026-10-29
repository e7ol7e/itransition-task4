"""Ground-truth tests of AI interpretation, not model-judged answer quality.

--live uses the configured provider/key. No key or customer contact text is saved.
Run in the existing VPS web container to keep its key on that host.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from assistant import Analyst
from data_tools import DashboardState, Filter, StoreTools, filtered


def cases(data):
    result = []
    for i in range(1, 6):
        store = f"STORE{i}"
        for key, question in [
            (
                "people",
                "How many real people are in the full customer directory, including those who never purchased?",
            ),
            (
                "records",
                "How many user records are in the directory, before reconciling aliases?",
            ),
            ("orders", "How many orders were placed? Count purchases, not copies."),
            (
                "copies",
                "How many copies of books were sold, including quantities greater than one?",
            ),
        ]:
            result.append(
                {
                    "id": f"{store.lower()}-{key}",
                    "mode": key,
                    "question": question,
                    "state": DashboardState(store=store),
                }
            )
    for key, question in [
        ("prefix", "How many customer records have an email starting with s or p?"),
        (
            "negative",
            "How many customer records have an email NOT starting with s or p? Exclude unknown emails.",
        ),
        ("contains", 'How many catalog titles contain the literal text "the"?'),
        (
            "not_contains",
            'How many catalog titles do NOT contain the literal text "the"? Exclude unknown titles.',
        ),
    ]:
        result.append(
            {"id": key, "mode": key, "question": question, "state": DashboardState()}
        )
    for key, question in [
        ("show_prefix", "Show customer records whose emails begin with s OR p."),
        (
            "hide_prefix",
            "Show customer records excluding emails beginning with s or p. Exclude unknown emails.",
        ),
    ]:
        result.append(
            {"id": key, "mode": key, "question": question, "state": DashboardState()}
        )
    day = data.results["STORE2"]["summary"]["top_days"][0]["date"]
    state = DashboardState(
        store="STORE2",
        filter_dataset="orders",
        filters=[{"field": "date", "op": "eq", "value": day}],
    )
    result.extend(
        [
            {
                "id": "filtered-orders",
                "mode": "filtered",
                "question": "How many orders are in the currently filtered view?",
                "state": state,
            },
            {
                "id": "whole-store-despite-filter",
                "mode": "orders",
                "question": "How many orders are in the WHOLE store, ignoring the current dashboard filters?",
                "state": state,
            },
            {
                "id": "clear-filters",
                "mode": "clear",
                "question": "Clear all filters and show the overview for this same store.",
                "state": state,
            },
            {
                "id": "aliases",
                "mode": "aliases",
                "question": f"List every alias ID and total lifetime spending for user {data.results['STORE1']['summary']['best_buyers'][0]['ids'][-1]}.",
                "state": DashboardState(),
            },
        ]
    )
    return result


def ground_truth(data, case):
    tables = data.tables[case["state"].store]
    mode = case["mode"]
    u, o, b = tables["users"], tables["orders"], tables["books"]
    if mode == "people":
        return int(u.person_id.nunique())
    if mode == "records":
        return len(u)
    if mode == "orders":
        return len(o)
    if mode == "copies":
        return int(o.quantity.sum())
    if mode in ("prefix", "negative", "show_prefix", "hide_prefix"):
        mask = u.email.str.casefold().str.startswith(("s", "p"), na=False)
        if mode in ("negative", "hide_prefix"):
            mask = ~mask & u.email.notna()
        return (
            sorted(int(v) for v in u.loc[mask, "id"])
            if mode.startswith(("show", "hide"))
            else int(mask.sum())
        )
    if mode in ("contains", "not_contains"):
        mask = b.title.str.casefold().str.contains("the", regex=False, na=False)
        if mode == "not_contains":
            mask = ~mask & b.title.notna()
        return int(mask.sum())
    if mode == "filtered":
        return len(data.scope(case["state"])["orders"])
    if mode == "clear":
        return {"store": case["state"].store, "view": "overview", "filters": []}
    winner = data.results["STORE1"]["summary"]["best_buyers"][0]
    return {"ids": winner["ids"], "spending_cents": winner["spending_cents"]}


def score(data, case, response):
    expected = ground_truth(data, case)
    mode = case["mode"]
    store = case["state"].store
    traces = [
        t
        for t in response.get("evidence", [])
        if "error" not in t and t["tool"] != "describe_data"
    ]
    if mode in ("show_prefix", "hide_prefix", "clear"):
        actions = response.get("actions", [])
        if not actions:
            return False, "No dashboard action"
        state = DashboardState.model_validate(actions[-1])
        if state.store != store:
            return False, "Wrong store"
        if mode == "clear":
            return (state.view == "overview" and not state.filters), state.model_dump()
        if state.view != "users" or state.filter_dataset != "users":
            return False, "Wrong dashboard dataset"
        actual = sorted(
            int(x) for x in filtered(data.tables[store]["users"], state.filters)["id"]
        )
        return actual == expected, {
            "matching_records": len(actual),
            "expected_records": len(expected),
            "filters": [f.model_dump() for f in state.filters],
        }
    if len(traces) != 1:
        return False, f"Expected one factual calculation, got {len(traces)}"
    t = traces[0]
    a = t["arguments"]
    r = t["result"]
    if a.get("store") != store:
        return False, "Wrong store"
    metric = {"people": "unique_users", "orders": "orders", "copies": "units_sold"}.get(
        mode
    )
    if metric and t["tool"] == "get_store_summary":
        return a.get("metrics") == [metric] and r.get(metric) == expected, {
            "metric": a.get("metrics"),
            "value": r.get(metric),
        }
    if mode == "aliases":
        ok = (
            t["tool"] == "get_user_aliases"
            and r.get("ids") == expected["ids"]
            and r.get("spending_cents") == expected["spending_cents"]
        )
        return ok, {"ids": r.get("ids"), "spending_cents": r.get("spending_cents")}
    if t["tool"] != "query_data":
        return False, "Expected a data query"
    dataset = (
        "users"
        if mode in ("people", "records", "prefix", "negative")
        else "books"
        if mode in ("contains", "not_contains")
        else "orders"
    )
    aggregates = a.get("aggregates", [])
    if a.get("dataset") != dataset or a.get("group_by") or len(aggregates) != 1:
        return False, "Wrong dataset, grouping or aggregate count"
    aggregate = aggregates[0]
    good = (
        (aggregate["op"] == "nunique" and aggregate.get("field") == "person_id")
        if mode == "people"
        else (aggregate["op"] == "sum" and aggregate.get("field") == "quantity")
        if mode == "copies"
        else aggregate["op"] in {"count", "nunique"}
    )
    if not good:
        return False, {"error": "Wrong aggregation meaning", "aggregate": aggregate}
    if mode in ("people", "records", "orders", "copies") and a.get("filters"):
        return False, "Whole-store question incorrectly filtered"
    # Compare actual matching record IDs as well as counts: equal counts can hide wrong filters.
    actual_frame = filtered(
        data.tables[store][dataset],
        [Filter.model_validate(f) for f in a.get("filters", [])],
    )
    if mode not in {"people", "copies"}:
        field = aggregate.get("field")
        # Equivalent plans are valid: count(non-null column) and distinct
        # primary-key count both count rows. Distinct person_id does not.
        counts_rows = (
            aggregate["op"] == "count"
            and (
                field is None
                or (field in actual_frame and actual_frame[field].notna().all())
            )
        ) or (aggregate["op"] == "nunique" and field == "id")
        if not counts_rows:
            return False, {
                "error": "Aggregation is not a record count",
                "aggregate": aggregate,
            }
    if mode == "filtered":
        wanted = data.scope(case["state"])["orders"]
    elif mode in ("prefix", "negative"):
        u = data.tables[store]["users"]
        mask = u.email.str.casefold().str.startswith(("s", "p"), na=False)
        if mode == "negative":
            mask = ~mask & u.email.notna()
        wanted = u.loc[mask]
    elif mode in ("contains", "not_contains"):
        b = data.tables[store]["books"]
        mask = b.title.str.casefold().str.contains("the", regex=False, na=False)
        if mode == "not_contains":
            mask = ~mask & b.title.notna()
        wanted = b.loc[mask]
    else:
        wanted = data.tables[store][dataset]
    field = aggregate.get("field")
    key = f"{aggregate['op']}_{field}" if field else "row_count"
    value = r.get("rows", [{}])[0].get(key)
    return sorted(actual_frame.id.tolist()) == sorted(
        wanted.id.tolist()
    ) and value == expected, {
        "value": value,
        "filters": a.get("filters", []),
        "aggregate": aggregate,
    }


def public_report(report):
    """Publish measured outcomes without prompts, record IDs or spending."""
    return {
        **{
            key: report[key]
            for key in ("model", "measured_at", "passed", "total", "planned")
        },
        "detail_policy": "Public verification omits prompts and data-specific results. Detailed diagnostics stay private.",
        "cases": [
            {key: row[key] for key in ("id", "store", "passed", "tools", "seconds")}
            for row in report["cases"]
        ],
    }


async def evaluate(path, only=None, private_details=False):
    data = StoreTools()
    analyst = Analyst(data)
    if not analyst.api_key:
        raise RuntimeError(
            "Set DEEPSEEK_API_KEY, or run inside the existing VPS container."
        )
    selected = [c for c in cases(data) if not only or c["id"] in only]
    results = []
    slots = asyncio.Semaphore(2)

    async def run(case):
        async with slots:
            start = time.perf_counter()
            try:
                reply = await asyncio.wait_for(
                    analyst.chat(case["question"], case["state"], []), timeout=150
                )
                passed, actual = score(data, case, reply)
                tools = [t["tool"] for t in reply["evidence"]]
            except Exception as error:  # noqa: BLE001 -- A failed provider call is a failed case, never silently skipped.
                passed = False
                actual = type(error).__name__ + ": " + str(error)[:250]
                tools = []
            row = {
                "id": case["id"],
                "question": case["question"],
                "store": case["state"].store,
                "passed": bool(passed),
                "actual": actual,
                "tools": tools,
                "seconds": round(time.perf_counter() - start, 2),
            }
            results.append(row)
            report = {
                "model": analyst.model,
                "measured_at": datetime.now(UTC).isoformat(),
                "passed": sum(r["passed"] for r in results),
                "total": len(results),
                "planned": len(selected),
                "cases": sorted(results, key=lambda r: r["id"]),
            }
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    report if private_details else public_report(report),
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf8",
            )
            temporary.replace(path)
            print(("PASS" if passed else "FAIL") + " " + case["id"], flush=True)

    await asyncio.gather(*(run(c) for c in selected))
    return all(r["passed"] for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/assistant-evaluation.json")
    )
    parser.add_argument("--only", nargs="*")
    parser.add_argument(
        "--private-details",
        action="store_true",
        help="Include prompts and detailed results; keep output outside public source.",
    )
    args = parser.parse_args()
    raise SystemExit(
        0 if asyncio.run(evaluate(args.output, args.only, args.private_details)) else 1
    )
