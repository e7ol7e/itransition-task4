"""Meaningful regression checks for tool arithmetic, validation and AI grounding."""
import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import app
from assistant import Analyst
from data_tools import Aggregate, DashboardState, Filter, Query, StoreTools


class ToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = StoreTools()

    def test_all_store_money_and_identity_counts_match_original_analysis(self):
        for store, result in self.data.results.items():
            with self.subTest(store=store):
                money = self.data.query(Query(store=store, dataset="orders", aggregates=[Aggregate(op="sum", field="paid_price_cents")]))
                people = self.data.query(Query(store=store, dataset="users", aggregates=[Aggregate(op="nunique", field="person_id")]))
                self.assertEqual(money["rows"][0]["sum_paid_price_cents"], result["summary"]["revenue_cents"])
                self.assertEqual(people["rows"][0]["nunique_person_id"], result["summary"]["unique_users"])

    def test_email_prefix_or_is_not_and(self):
        query = Query(store="STORE1", dataset="users", filters=[Filter(field="email", op="starts_with_any", value=["s", "p"])], aggregates=[Aggregate(op="count")])
        expected = int(self.data.tables["STORE1"]["users"]["email"].str.startswith(("s", "p"), na=False).sum())
        result = self.data.query(query)
        self.assertGreater(expected, 0)
        self.assertEqual(result["rows"][0]["row_count"], expected)

    def test_date_range_and_filtered_buyer_spending(self):
        date = self.data.results["STORE1"]["summary"]["top_days"][0]["date"]
        state = DashboardState(store="STORE1", filters=[Filter(field="date", op="between", value=[date, date])])
        result = self.data.dashboard(state)["summary"]
        orders = self.data.tables["STORE1"]["orders"]
        expected = orders.loc[orders["date"] == date]
        self.assertEqual(result["revenue_cents"], int(expected["paid_price_cents"].sum()))
        self.assertEqual(result["best_buyers"][0]["spending_cents"], int(expected.groupby("person_id")["paid_price_cents"].sum().max()))

    def test_unknown_fields_and_executable_expressions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.data.query(Query(store="STORE1", dataset="users", filters=[Filter(field="__import__('os')", op="eq", value=1)]))
        with self.assertRaises(ValidationError):
            Query(store="STORE1", dataset="users", sql="delete from users")
        with self.assertRaises(ValueError):
            self.data.query(Query(store="STORE1", dataset="users", aggregates=[Aggregate(op="sum", field="name")]))

    def test_top_author_ranking_and_all_aliases(self):
        store="STORE1"
        ranking=self.data.query(Query(store=store,dataset="orders",group_by=["author_set"],aggregates=[Aggregate(op="sum",field="quantity")],sort_by="sum_quantity",descending=True,limit=1))
        self.assertEqual(ranking["rows"][0]["sum_quantity"],self.data.results[store]["summary"]["popular_units"])
        winner=self.data.results[store]["summary"]["best_buyers"][0]
        self.assertEqual(self.data.aliases(store,winner["ids"][-1])["ids"],winner["ids"])

    def test_unfiltered_overview_preserves_every_original_metric(self):
        original=self.data.results["STORE3"]["summary"]
        result=self.data.dashboard(DashboardState(store="STORE3"))["summary"]
        self.assertEqual({k:v for k,v in result.items() if k!="filtered"},original)

    def test_model_cannot_replace_verified_numbers_with_hallucinated_prose(self):
        analyst=Analyst(self.data)
        replies=iter([
            {"role":"assistant","content":None,"tool_calls":[{"id":"test","type":"function","function":{"name":"get_store_summary","arguments":json.dumps({"store":"STORE1","metrics":["unique_users"]})}}]},
            {"role":"assistant","content":"There are 999999999 users."},
        ])
        async def fake_completion(*args,**kwargs):return next(replies)
        analyst.completion=fake_completion
        result=asyncio.run(analyst.chat("How many real users?",DashboardState(),[]))
        self.assertIn(str(self.data.results["STORE1"]["summary"]["unique_users"]),result["answer"])
        self.assertNotIn("999999999",result["answer"])

    def test_control_action_contains_validated_real_matching_count(self):
        analyst=Analyst(self.data)
        state={"store":"STORE2","view":"users","filter_dataset":"users","filters":[{"field":"email","op":"starts_with_any","value":["s","p"]}]}
        result=analyst.execute("update_dashboard",state)
        self.assertGreater(result["matching_rows"],0)
        self.assertEqual(result["state"]["store"],"STORE2")
        with self.assertRaises(ValueError):analyst.execute("update_dashboard",dict(state,filters=[{"field":"password","op":"eq","value":"x"}]))

    def test_semantic_title_filter_validates_ids_and_counts_without_model_arithmetic(self):
        analyst = Analyst(self.data)

        class FakeClient:
            def __init__(self, *args, **kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self, *args):pass
            async def post(self, url, **kwargs):
                batch = json.loads(kwargs['json']['messages'][1]['content'])['titles']
                selection = [batch[0]['id']]
                return type('Reply', (), {'status_code': 200, 'json': lambda self: {
                    'choices': [{'message': {'content': json.dumps({'ids': selection})}}]}})()

        with patch('assistant.httpx.AsyncClient', FakeClient):
            result = asyncio.run(analyst.semantic_books({'store':'STORE1', 'criterion':"contains a person's name"}))
        ids = result['state']['filters'][0]['value']
        self.assertEqual(result['matching_rows'], len(ids))
        self.assertGreater(len(ids), 1)
        self.assertTrue(set(ids).issubset(set(self.data.tables['STORE1']['books']['id'])))

        class InvalidClient(FakeClient):
            async def post(self, *args, **kwargs):
                return type('Reply', (), {'status_code': 200, 'json': lambda self: {
                    'choices': [{'message': {'content': '{"ids": [-999999]}'}}]}})()

        with patch('assistant.httpx.AsyncClient', InvalidClient), self.assertRaises(ValueError):
            asyncio.run(analyst.semantic_books({'store':'STORE1', 'criterion':'another criterion'}))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context=TestClient(app)
        cls.client=cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None,None,None)

    def test_dashboard_and_health(self):
        self.assertEqual(self.client.get('/healthz').text,'ok\n')
        self.assertIn('Your data analyst',self.client.get('/').text)
        self.assertEqual(self.client.get('/static/app.js').status_code,200)
        response=self.client.post('/api/dashboard',json={"store":"STORE5","view":"books"})
        self.assertEqual(response.status_code,200)
        self.assertGreater(response.json()['table']['matched_rows'],100)

    def test_request_validation(self):
        self.assertEqual(self.client.post('/api/query',json={"store":"OTHER","dataset":"users"}).status_code,422)
        self.assertEqual(self.client.post('/api/chat',json={"message":"","history":[]}).status_code,422)


if __name__=='__main__':unittest.main()
