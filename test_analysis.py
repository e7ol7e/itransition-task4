"""Run: python -m unittest discover -s task4 -p test_analysis.py -v"""

import unittest
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pandas as pd

from analysis import (analyze_store, author_set, check_ids, clean_orders, clean_users,
                      parse_price, parse_timestamps, reconcile_users)


class CleaningTests(unittest.TestCase):
    def test_all_price_notations(self):
        for text in ["$70.50", "USD 70.5", "70.50 USD", "70.50$", "$70\u00a250", "70$50\u00a2"]:
            with self.subTest(text=text):
                self.assertEqual(parse_price(text), (Decimal("70.50"), "USD"))
        for text in ["\u20ac70.50", "EUR 70.5", "70.50EUR", "70.50 \u20ac", "\u20ac70\u00a250", "70\u20ac50\u00a2"]:
            self.assertEqual(parse_price(text), (Decimal("70.50"), "EUR"))
        self.assertEqual(parse_price("USD35."), (Decimal(35), "USD"))
        for invalid in ["12", "GBP12", "$-2", "$1.234", "$NaN", "$2junk", "$2\u00a2100"]:
            with self.assertRaises(ValueError):
                parse_price(invalid)

    def test_round_completed_line_and_quarantine(self):
        raw = pd.DataFrame({"id": [1, 2, 3, 4], "user_id": [1]*4, "book_id": [1]*4,
                            "quantity": [3, 1, 0, 1], "unit_price": ["EUR0.99", "$0.01", "$5", "$5"],
                            "timestamp": ["2025-01-02"]*3 + ["bad date"], "shipping": [None]*4})
        orders, rejected = clean_orders(raw)
        self.assertEqual(orders["paid_price_cents"].tolist(), [356, 1])
        self.assertEqual(orders["paid_price"].tolist(), [Decimal("3.56"), Decimal("0.01")])
        self.assertEqual(rejected["id"].tolist(), [3, 4])

    def test_dates(self):
        result = parse_timestamps(pd.Series(["12/04/25 3:30 P.M.", "4-July-2025", "2025.12.04", "bad"]))
        self.assertEqual(result.iloc[0], pd.Timestamp("2025-12-04 15:30"))
        self.assertEqual(result.iloc[1], pd.Timestamp("2025-07-04"))
        self.assertEqual(result.iloc[2], pd.Timestamp("2025-12-04"))
        self.assertTrue(pd.isna(result.iloc[3]))

    def test_unordered_authors(self):
        self.assertEqual(author_set("Paul, John, Paul"), author_set(" John, Paul "))
        self.assertNotEqual(author_set("John"), author_set("John, Paul"))
        self.assertEqual(author_set(pd.NA), ())

    def test_identity_chains_and_missing_evidence(self):
        raw = pd.DataFrame([
            [1, "Alice", "A", "(123)456-7890", "A@EXAMPLE.COM"],
            [2, "Alias", "A", "1234567890", "a@example.com"],
            [3, "Alias", "B", "1234567890", "a@example.com"],
            [4, "Other", None, "5550000000", "b@example.com"],
            [5, "Another", None, "5550000000", "b@example.com"],
        ], columns=["id", "name", "address", "phone", "email"])
        mapping, _ = reconcile_users(clean_users(raw))
        self.assertEqual(mapping["person_id"].tolist(), [1, 1, 1, 4, 5])

    def test_conflicting_id_is_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            check_ids(pd.DataFrame({"id": [1, 1], "name": ["a", "b"]}), "users")


class DatasetTests(unittest.TestCase):
    def test_all_five_stores_against_independent_totals(self):
        for folder in sorted((Path(__file__).parent / "data").glob("STORE*")):
            with self.subTest(store=folder.name):
                result = analyze_store(folder)
                summary, orders, users = result["summary"], result["orders"], result["users"]
                self.assertEqual(len(result["rejected"]), 0)
                self.assertEqual(len(orders), len(pd.read_parquet(folder / "orders.parquet")))
                # A Python dictionary accumulator independently checks pandas grouping/ranking.
                days, buyers, authors = Counter(), Counter(), Counter()
                for row in orders.itertuples():
                    days[row.date.strftime("%Y-%m-%d")] += int(row.paid_price_cents)
                    buyers[int(row.person_id)] += int(row.paid_price_cents)
                    authors[row.author_set] += int(row.quantity)
                expected = sorted(days.items(), key=lambda item: (-item[1], item[0]))[:5]
                self.assertEqual([(row["date"], row["revenue_cents"]) for row in summary["top_days"]], expected)
                self.assertEqual(summary["revenue_cents"], sum(days.values()))
                self.assertEqual(summary["popular_units"], max(authors.values()))
                self.assertEqual(summary["best_buyers"][0]["spending_cents"], max(buyers.values()))
                # Independent identity check: count shared individual fields per candidate pair,
                # then walk the resulting graph (rather than using three-column signatures).
                matches = Counter()
                for column in ["name", "address", "phone", "email"]:
                    groups = defaultdict(list)
                    for row in users[["id", column]].itertuples(index=False, name=None):
                        if pd.notna(row[1]):
                            groups[str(row[1]).casefold()].append(int(row[0]))
                    for group in groups.values():
                        for i, first in enumerate(group):
                            for second in group[i+1:]:
                                matches[tuple(sorted((first, second)))] += 1
                graph = defaultdict(set)
                for (first, second), count in matches.items():
                    if count >= 3:
                        graph[first].add(second)
                        graph[second].add(first)
                expected_mapping = {}
                for user_id in users["id"]:
                    if user_id in expected_mapping:
                        continue
                    component, todo = set(), [int(user_id)]
                    while todo:
                        current = todo.pop()
                        if current in component:
                            continue
                        component.add(current)
                        todo.extend(graph[current] - component)
                    expected_mapping.update({member: min(component) for member in component})
                self.assertEqual(dict(result["mapping"].itertuples(index=False, name=None)), expected_mapping)


if __name__ == "__main__":
    unittest.main()
