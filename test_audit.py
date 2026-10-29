"""Evidence must reconcile to cards and preserve identity/filter semantics."""

import asyncio
import json
import unittest

import pandas as pd
from fastapi.testclient import TestClient

from analysis import clean_users, reconcile_users
from app import app
from assistant import Analyst
from audit import AuditRequest, explain
from data_tools import DashboardState, Filter, StoreTools, filtered
from evaluate_assistant import cases, ground_truth, public_report, score


class PublicReportTests(unittest.TestCase):
    def test_details_never_enter_public_report(self):
        report = {
            "model": "test",
            "measured_at": "test",
            "passed": 0,
            "total": 1,
            "planned": 1,
            "cases": [
                {
                    "id": "aliases",
                    "store": "STORE1",
                    "passed": False,
                    "tools": [],
                    "seconds": 1,
                    "question": "private customer prompt",
                    "actual": {"ids": [123], "spending_cents": 456},
                    "future_private_field": "sensitive",
                }
            ],
        }
        result = public_report(report)
        self.assertEqual(result["passed"], 0)
        self.assertEqual(
            set(result["cases"][0]), {"id", "store", "passed", "tools", "seconds"}
        )
        self.assertIn("actual", report["cases"][0])


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = StoreTools()

    def test_five_stores_and_filtered_cards_reconcile(self):
        for store in self.data.tables:
            day = self.data.results[store]["summary"]["top_days"][0]["date"]
            for filters in ([], [Filter(field="date", op="eq", value=day)]):
                state = DashboardState(store=store, filters=filters)
                summary = self.data.dashboard(state)["summary"]
                identity = explain(
                    self.data, AuditRequest(state=state, kind="identity")
                )
                revenue = explain(self.data, AuditRequest(state=state, kind="revenue"))
                authors = explain(self.data, AuditRequest(state=state, kind="authors"))
                self.assertEqual(
                    identity["stats"]["unique_people"], summary["unique_users"]
                )
                self.assertEqual(revenue["revenue_cents"], summary["revenue_cents"])
                self.assertEqual(revenue["copies"], summary["units_sold"])
                self.assertEqual(revenue["order_count"], summary["orders"])
                self.assertEqual(
                    authors["unique_author_sets"], summary["unique_author_sets"]
                )

    def test_all_aliases_and_scoped_spending(self):
        winner = self.data.results["STORE1"]["summary"]["best_buyers"][0]
        state = DashboardState(
            filters=[
                Filter(
                    field="date",
                    op="eq",
                    value=self.data.tables["STORE1"]["orders"]
                    .loc[lambda x: x.person_id == winner["ids"][0], "date"]
                    .iloc[0],
                )
            ]
        )
        person = explain(
            self.data,
            AuditRequest(kind="person", state=state, user_id=winner["ids"][-1]),
        )
        self.assertEqual([r["id"] for r in person["records"]], winner["ids"])
        self.assertEqual(person["lifetime_spending_cents"], winner["spending_cents"])
        ledger = explain(
            self.data,
            AuditRequest(kind="buyer", state=state, user_id=winner["ids"][-1]),
        )
        self.assertEqual(ledger["revenue_cents"], person["scoped_spending_cents"])
        self.assertLess(
            person["scoped_spending_cents"], person["lifetime_spending_cents"]
        )

    def test_minimal_graph_covers_every_alias_and_never_uses_unknowns(self):
        groups = explain(self.data, AuditRequest(kind="identity", limit=100))
        for group in groups["rows"]:
            p = explain(
                self.data, AuditRequest(kind="person", user_id=group["person_id"])
            )
            self.assertEqual(len(p["edges"]), len(p["records"]) - 1)
            nodes = set()
            for edge in p["edges"]:
                nodes.update([edge["id_a"], edge["id_b"]])
                self.assertGreaterEqual(len(edge["matched_fields"]), 3)
                self.assertFalse(
                    set(edge["matched_fields"]) & set(edge["unknown_fields"])
                )
            self.assertEqual(nodes, set(group["ids"]))

    def test_transitive_chain_explained_without_claiming_endpoints_match(self):
        users = clean_users(
            pd.DataFrame(
                [
                    [1, "Alice", "A", "123", "a@example.test"],
                    [2, "Alias", "A", "123", "a@example.test"],
                    [3, "Alias", "B", "123", "a@example.test"],
                    [4, "Other", None, "999", "b@example.test"],
                    [5, "Another", None, "999", "b@example.test"],
                ],
                columns=["id", "name", "address", "phone", "email"],
            )
        )
        mapping, links = reconcile_users(users)
        people = users.merge(mapping, left_on="id", right_on="user_id")
        data = StoreTools.__new__(StoreTools)
        data.tables = {
            "STORE1": {
                "users": people,
                "orders": pd.DataFrame(columns=["person_id", "paid_price_cents"]),
                "books": pd.DataFrame(),
            }
        }
        data.results = {"STORE1": {"links": links}}
        result = explain(data, AuditRequest(kind="person", user_id=3))
        self.assertEqual([r["id"] for r in result["records"]], [1, 2, 3])
        self.assertEqual(
            {(e["id_a"], e["id_b"]) for e in result["edges"]}, {(1, 2), (2, 3)}
        )
        self.assertIn("endpoints do not directly", result["method"])
        single = explain(data, AuditRequest(kind="person", user_id=4))
        self.assertEqual(len(single["records"]), 1)

    def test_ledger_rounding_and_pagination_totals(self):
        a = explain(self.data, AuditRequest(kind="revenue", limit=25))
        b = explain(self.data, AuditRequest(kind="revenue", offset=25, limit=25))
        self.assertEqual(a["revenue_cents"], b["revenue_cents"])
        self.assertEqual(a["order_count"], 10493)
        self.assertTrue(a["has_more"])
        self.assertFalse({r["id"] for r in a["rows"]} & {r["id"] for r in b["rows"]})
        eur = next(r for r in a["rows"] + b["rows"] if r["currency"] == "EUR")
        self.assertEqual(eur["exchange_rate"], "1.2")

    def test_negative_text_filters_exclude_unknown_and_not_just_exact_s(self):
        frame = pd.DataFrame({"email": ["sue@test", "pete@test", "bob@test", None]})
        negative = filtered(
            frame, [Filter(field="email", op="not_starts_with_any", value=["s", "p"])]
        )
        self.assertEqual(negative.email.tolist(), ["bob@test"])
        literal = filtered(frame, [Filter(field="email", op="ne", value="s")])
        self.assertNotEqual(negative.email.tolist(), literal.email.tolist())
        self.assertEqual(
            filtered(
                frame, [Filter(field="email", op="not_contains", value="test")]
            ).email.tolist(),
            [],
        )

    def test_ai_scorer_rejects_plausible_wrong_meaning(self):
        case = next(c for c in cases(self.data) if c["id"] == "store1-records")
        wrong = {
            "evidence": [
                {
                    "tool": "get_store_summary",
                    "arguments": {"store": "STORE1", "metrics": ["unique_users"]},
                    "result": {"unique_users": ground_truth(self.data, case)},
                }
            ]
        }
        self.assertFalse(score(self.data, case, wrong)[0])
        correct = {
            "evidence": [
                {
                    "tool": "query_data",
                    "arguments": {
                        "store": "STORE1",
                        "dataset": "users",
                        "aggregates": [{"op": "count"}],
                    },
                    "result": {"rows": [{"row_count": ground_truth(self.data, case)}]},
                }
            ]
        }
        self.assertTrue(score(self.data, case, correct)[0])
        for op, field in [("count", "person_id"), ("nunique", "id")]:
            equivalent = {
                "evidence": [
                    {
                        "tool": "query_data",
                        "arguments": {
                            "store": "STORE1",
                            "dataset": "users",
                            "aggregates": [{"op": op, "field": field}],
                        },
                        "result": {
                            "rows": [{f"{op}_{field}": ground_truth(self.data, case)}]
                        },
                    }
                ]
            }
            self.assertTrue(score(self.data, case, equivalent)[0])
        negative = next(c for c in cases(self.data) if c["id"] == "negative")
        wrong_query = {
            "evidence": [
                {
                    "tool": "query_data",
                    "arguments": {
                        "store": "STORE1",
                        "dataset": "users",
                        "filters": [{"field": "email", "op": "ne", "value": "s"}],
                        "aggregates": [{"op": "count"}],
                    },
                    "result": {
                        "rows": [{"row_count": ground_truth(self.data, negative)}]
                    },
                }
            ]
        }
        self.assertFalse(score(self.data, negative, wrong_query)[0])

    def test_only_explicitly_selected_results_are_presented(self):
        analyst = Analyst(self.data)

        def call(id, name, args):
            return {
                "id": id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }

        replies = iter(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        call(
                            "explore",
                            "get_store_summary",
                            {"store": "STORE1", "metrics": ["orders"]},
                        ),
                        call(
                            "final",
                            "get_store_summary",
                            {"store": "STORE1", "metrics": ["units_sold"]},
                        ),
                    ],
                },
                {
                    "role": "assistant",
                    "tool_calls": [
                        call(
                            "finish", "finish_analysis", {"result_ids": ["invented-id"]}
                        )
                    ],
                },
                {
                    "role": "assistant",
                    "tool_calls": [
                        call("finish2", "finish_analysis", {"result_ids": ["final"]})
                    ],
                },
            ]
        )

        async def fake(*args, **kwargs):
            return next(replies)

        analyst.completion = fake
        result = asyncio.run(analyst.chat("How many copies?", DashboardState(), []))
        self.assertEqual([t["id"] for t in result["evidence"]], ["final"])
        self.assertIn("Copies sold", result["answer"])
        self.assertNotIn("Orders:", result["answer"])
        self.assertTrue(any(t.get("error") for t in result["trace"]))

    def test_intermediate_dashboard_actions_are_not_applied(self):
        analyst = Analyst(self.data)

        def call(id, name, args):
            return {
                "id": id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }

        first = DashboardState(
            view="users",
            filter_dataset="users",
            filters=[Filter(field="email", op="starts_with", value="s")],
        ).model_dump()
        last = DashboardState(
            view="users",
            filter_dataset="users",
            filters=[Filter(field="email", op="not_starts_with_any", value=["s", "p"])],
        ).model_dump()
        replies = iter(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        call("first", "update_dashboard", first),
                        call("last", "update_dashboard", last),
                    ],
                },
                {
                    "role": "assistant",
                    "tool_calls": [
                        call("finish", "finish_analysis", {"result_ids": ["last"]})
                    ],
                },
            ]
        )

        async def fake(*args, **kwargs):
            return next(replies)

        analyst.completion = fake
        result = asyncio.run(
            analyst.chat("Exclude emails starting with s or p", DashboardState(), [])
        )
        self.assertEqual(result["actions"], [last])


class AuditHTTPTests(unittest.TestCase):
    def test_audit_validation_and_static_asset(self):
        with TestClient(app) as client:
            self.assertEqual(
                client.post(
                    "/api/audit", json={"kind": "person", "user_id": -123}
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/audit", json={"kind": "day", "day": "not a date"}
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/audit", json={"kind": "identity", "limit": 1000000}
                ).status_code,
                422,
            )
            self.assertEqual(client.get("/static/audit.js").status_code, 200)
            home = client.get("/")
            self.assertEqual(home.headers["cache-control"], "no-cache")
            self.assertIn("/static/audit.js?v=", home.text)
            response = client.post("/api/audit", json={"kind": "identity"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["stats"]["unique_people"], 3221)


if __name__ == "__main__":
    unittest.main()
