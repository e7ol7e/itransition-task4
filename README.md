# Task 4 · Bookstore analysis

Sales analysis for five stores, with a dashboard and DeepSeek tool calls for
questions and filters.

- Dashboard: https://task4.evgeniy.win
- Source: https://github.com/e7ol7e/itransition-task4

The implementation is in `analysis.py`, `data_tools.py`, `assistant.py`, and `app.py`.
Personal learning notebooks and their row-level outputs stay local and are excluded
from this source repository.

## Run locally

From this directory, with Python 3.15 available:

```powershell
uv sync
uv run python analysis.py
uv run python -m unittest discover -s . -p "test_*.py" -v
uv run uvicorn app:app --host 127.0.0.1 --port 8764
```

Open http://127.0.0.1:8764. The original aggregate charts are embedded in the page;
interactive tables, filtering, and chat use the FastAPI service.

For local chat, copy `.env.example` to `task4.env`, supply your own key, and run:

```powershell
uv run --env-file task4.env uvicorn app:app --host 127.0.0.1 --port 8764
```

The key is read server-side from the environment. Keep `task4.env` outside source
control and restrict its file permissions.

The input archive must be extracted into `data/STORE1/` through `data/STORE5/`.
Each directory contains `users.csv`, `orders.parquet`, and `books.yaml`.
Input data and detailed local outputs are excluded from Git; the generated
dashboard contains the aggregate results necessary to view the submission.

`analysis.py` contains the cleaning and aggregation code. The optional notebook
helpers, `update_notebook.py` and `run_notebook.py`, support local study; the server
does not use notebooks.

## Calculation decisions

- Each store is independent. User IDs are not treated as global across stores.
- Text is normalized, missing markers remain unknown, and phone punctuation is
  removed. Identical copies can be removed; conflicting primary keys raise an error.
- People are connected when any three known normalized personal fields match:
  name, address, phone, email. Transitive links retain every alias. Missing values
  do not provide identity evidence. This uses the assignment's one-field-change
  assumption; it is not a general-purpose real-world identity verification system.
- Prices support the supplied currency prefixes, suffixes and cent notation.
  USD conversion uses Decimal and the fixed EUR 1 = USD 1.2 rate. Each completed
  quantity × price line is rounded once to cents, half up. All totals use integer
  cents. Unit prices can retain more precision before the completed line is rounded.
- Mixed timestamps retain local source dates. Slash dates are MM/DD/YY, supported
  by unambiguous source examples. Calendar dates without orders have zero revenue.
- Invalid optional publication years become nullable integers. Missing publishers,
  addresses and shipping are not filled with guessed values and do not invalidate sales.
- Invalid essential order fields are quarantined with reasons. All supplied orders
  pass. Unknown user/book references and conflicting IDs fail explicitly.
- Author names separated by commas form unordered sets; spelling is preserved for
  display. Catalog author-set counts include unsold books. Popularity sums copies
  sold for the entire set, rather than counting orders or splitting co-authors.
- User counts cover the full directory. Best-buyer spending joins every order to
  its reconciled person and lists all of that person's IDs, including unused aliases.
- All tied top customers and popular author sets are returned. Revenue-day ties
  are ordered by date ascending. Chart range buttons affect only the chart.

`output/summary.json` contains all calculated values. `output/STORE*/` contains
cleaned tables, identity mappings, matching evidence, rejected orders and daily
totals. These detailed exports stay local. The web container includes the input
files for server-side queries and serves only the built dashboard as static files.

## VPS deployment

The shared `/root/itransition/compose.yaml` has a service named `task4`:

```text
Cloudflare Tunnel (configured separately)
  -> http://127.0.0.1:5002  Caddy, on the VPS host
  -> http://127.0.0.1:5003  Docker-published loopback port
  -> task4 container:8080  unprivileged FastAPI, pandas tools, dashboard and chat
```

Both `/` and `/task4/` serve the dashboard. `/healthz` returns `ok`.
The container uses a read-only filesystem, writable temporary storage and no
Linux capabilities. It contains the original store inputs for server-side analysis;
these files are not mounted as public static assets. Source and image-building files live in `/root/itransition/task4/`.
The active Caddy configuration remains in `/etc/caddy/Caddyfile`.

After uploading updated source, input data, and built dashboard assets to the VPS:

```sh
cd /root/itransition
docker compose up -d --build --wait task4
curl -f http://127.0.0.1:5002/healthz
```

To inspect it before configuring Cloudflare, forward the local port:

```powershell
ssh -L 8765:127.0.0.1:5002 vps
```

Then open http://127.0.0.1:8765. The Cloudflare Tunnel service URL is **http://127.0.0.1:5002**.

## AI questions and dashboard controls

`assistant.py` uses DeepSeek's tool-calling API. It receives table schemas and
bounded tool results, not a dump of the store databases. Tools are defined by
Pydantic schemas and execute validated pandas operations in `data_tools.py`.
The model cannot execute Python, SQL, filesystem operations, or arbitrary URLs.

For factual questions the model selects a plan; the displayed answer is rendered
from successful tool output. Free-form final model prose is not used for numerical
answers, so displayed figures come from the selected computation. The model still chooses
the query; its filters and grouping are visible in the evidence inspector. Open
"Verified data" below an answer to inspect the exact arguments and results.

Supported operations include whole-store metrics, filtered aggregates, rankings,
customer alias lookup, record search, and grouping by date, author set, genre,
publisher, customer, or other described fields. Pagination is explicit; row samples
are never counted as complete results. Empty matches and unknown fields are handled
explicitly. All five stores remain independent.

The same tools control real dashboard state: store, overview/catalog/customers/orders,
filters, and chart range. Filters are visible and can be cleared manually. No source
records are changed. Example commands:

- "How many real unique customers are in Store 3?"
- "Which genre earned the most revenue in Store 2 in 2025?"
- "List all aliases and spending for a selected user in Store 1."
- "Show customers whose emails start with s or p."
- "Switch to Store 5 and show the last 30 days."
- "Filter out books whose titles contain people's names."
- "Clear filters and show the overview."

Semantic title filters classify bounded batches of **title text only**. Returned IDs
are checked against the actual catalog and all counts are calculated by pandas.
This semantic judgment is identified as AI classification; literal filters and
numerical metrics remain deterministic. Classification results are cached per process.

The service limits concurrent chat requests and requests per minute. Model and key
are configured through the environment; model/provider errors are shown without
exposing credentials. Endpoints are documented at `/docs`.

`test_app.py` checks query validation, exact money and identity counts for all stores,
OR-prefix filters, date filtering, aliases, frontend/API availability, and a deliberately
hallucinated model answer that must not replace the verified figure.

## Deployment and rollback

`deploy_vps.py` updates only `task4` in the shared `/root/itransition/compose.yaml`.
It keeps Caddy and other services intact, retains the previous image for rollback,
and verifies both the Docker and Caddy health endpoints. If the old image tag was
pruned while its container is still running, a container snapshot preserves rollback.

For submission, provide the live dashboard at https://task4.evgeniy.win and the
source repository at https://github.com/e7ol7e/itransition-task4.
The original input archive is intentionally excluded from Git; extract the supplied
assignment archive into `data/` to reproduce the full analysis.


## Inspectable evidence

Every overview metric has an **Explain this number** button. Revenue dates, winning buyers, author sets, customer IDs, and verified chat answers also open the evidence inspector.

- **Identity groups:** matching records versus real people; searchable, paginated alias groups, including a switch to show singletons.
- **Identity graph:** a minimal spanning set of links saved by reconciliation. Choose a numbered link to compare normalized records: green fields match, amber fields differ, unknown fields never count as evidence. Chains may connect endpoints that do not directly match three fields. This implements the assignment's one-field-change assumption, not general identity verification.
- **Purchases:** every alias contributes to the buyer total. Scoped and lifetime spending are labeled separately.
- **Revenue and copies:** paginated original orders with quantity, original price, FX rate, unrounded USD result, and line total. Summary totals cover every matching order, not only the visible page.
- **Author sets:** catalog title counts and quantity-weighted sales, including unsold sets in catalog counts.
- **Chat:** inspect the exact interpreted filters/grouping/aggregation and recompute the underlying query. Only selected final tool results appear as answers or dashboard actions; intermediate computations remain in the API trace.

Cards and audit evidence share `StoreTools.scope`, so active filters have one interpretation. Evidence downloads contain the request and current result page with explicit totals/pagination; they do not claim to export the whole dataset. The inspector uses `/api/audit`; no additional model call is needed to explain a number. Generated asset URLs include content versions, and HTML is revalidated so CDN caches cannot mix frontend releases.

## AI interpretation evaluation

`evaluate_assistant.py` contains 30 ground-truth cases. They distinguish directory records from real people, order counts from sold copies, positive from negative text filters, OR prefixes, filtered from whole-store questions, clear-filter commands, and lifetime spending across aliases. Five-store cases also verify the selected store.

The evaluator checks tool intent and actual matching record IDs, as well as computed results. A wrong filter that happens to produce the right count cannot pass. Expected answers come from deterministic pandas calculations; an AI model is never used to grade itself. Provider failures count as failures, not skipped cases. A passing run is evidence for these cases, not a guarantee about every possible question.

Run with the configured API key on the VPS:

```sh
cd /root/itransition
docker compose exec -T task4 /app/.venv/bin/python evaluate_assistant.py --live --output /tmp/assistant-evaluation.json
# Preserve the report outside the container tmpfs:
mkdir -p task4/evaluation
docker compose exec -T task4 cat /tmp/assistant-evaluation.json > task4/evaluation/assistant-results.json
```

The default report contains case labels, pass/fail, tool names and timings; it omits prompts and data-specific results. See `evaluation/assistant-results.json` for the measured run. For private debugging, add `--private-details` and save the output outside public source (for example under ignored `submission/`). Never publish credentials. Detailed diagnostics belong in ignored local files.

The ordinary offline suite also tests transitive identity chains, missing evidence, scoped totals, pagination, negative filters, rejected final-result references, and discarding intermediate dashboard actions:

```sh
uv run python -m unittest discover -s . -p "test_*.py" -v
```

`browser_checks.py` is an optional live UI check requiring Playwright and installed Chrome. Its provider reply is stubbed to exercise the chat inspector without API spending; evidence requests still hit the real backend. Live model behavior is tested separately by the evaluator above.
