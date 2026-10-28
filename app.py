"""Task 4 dashboard, validated analysis API, and tool-backed DeepSeek chat."""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from assistant import Analyst
from data_tools import DashboardState, Query, StoreTools, StrictModel, records

ROOT = Path(__file__).resolve().parent


class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2500)
    state: DashboardState = Field(default_factory=DashboardState)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)


@asynccontextmanager
async def lifespan(app):
    app.state.data = StoreTools()
    app.state.analyst = Analyst(app.state.data)
    app.state.chat_slots = asyncio.Semaphore(3)
    app.state.requests = defaultdict(deque)
    yield


app = FastAPI(title="Bookkeeping — Store Analytics", version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz", include_in_schema=False)
def health():
    return PlainTextResponse("ok\n")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/api/status")
def status():
    return {"ready": True, "ai_configured": bool(app.state.analyst.api_key),
            "model": app.state.analyst.model, "stores": list(app.state.data.tables)}


@app.post("/api/query")
def query(payload: Query):
    try:
        return app.state.data.query(payload)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/dashboard")
def dashboard(state: DashboardState):
    try:
        result = app.state.data.dashboard(state)
        dataset = "orders" if state.view == "overview" else state.view
        # The displayed table uses the same source filters as the overview.
        if dataset == state.filter_dataset:
            page = app.state.data.query(Query(store=state.store, dataset=dataset, filters=state.filters, limit=100))
        elif not state.filters:
            page = app.state.data.query(Query(store=state.store, dataset=dataset, limit=100))
        else:
            # Cross-table filtering is represented by selected book/person/order IDs.
            from data_tools import Filter, filtered
            tables = app.state.data.tables[state.store]
            selected = filtered(tables[state.filter_dataset], state.filters)
            orders = tables["orders"]
            if state.filter_dataset == "books":
                orders = orders.loc[orders["book_id"].isin(selected["id"])]
            elif state.filter_dataset == "users":
                orders = orders.loc[orders["person_id"].isin(selected["person_id"])]
            else:
                orders = selected
            if dataset == "books":
                match = tables["books"].loc[tables["books"]["id"].isin(orders["book_id"])]
            elif dataset == "users":
                match = tables["users"].loc[tables["users"]["person_id"].isin(orders["person_id"])]
            else:
                match = orders
            columns = {"books": ["id", "title", "author", "genre", "year"],
                       "users": ["id", "name", "email", "person_id"],
                       "orders": ["id", "date", "user_id", "book_id", "quantity", "paid_price_cents"]}[dataset]
            page = {"rows": records(match[columns].head(100)), "matched_rows": len(match),
                    "truncated": len(match) > 100, "result_rows": len(match)}
        result["table"] = page
        return result
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/schema/{store}")
def schema(store: Literal["STORE1", "STORE2", "STORE3", "STORE4", "STORE5"]):
    return app.state.data.describe(store)


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request):
    # Do not trust a client-supplied forwarded IP. Per-process limit also bounds
    # total public AI spending, regardless of proxy configuration.
    now = time.monotonic()
    queue = app.state.requests["global"]
    while queue and queue[0] < now - 60:
        queue.popleft()
    if len(queue) >= int(os.getenv("CHAT_REQUESTS_PER_MINUTE", "20")):
        raise HTTPException(429, "The analyst is busy. Please retry in a minute.")
    queue.append(now)
    try:
        async with app.state.chat_slots:
            return await asyncio.wait_for(app.state.analyst.chat(payload.message, payload.state,
                         [item.model_dump() for item in payload.history]), timeout=120)
    except (RuntimeError, asyncio.TimeoutError) as error:
        raise HTTPException(503, str(error) or "The AI request timed out. Please retry.") from error


@app.get("/", include_in_schema=False)
@app.get("/task4/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "dashboard" / "dist" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "dashboard" / "dist"), name="static")
