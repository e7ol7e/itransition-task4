# Task 4 · Bookstore analysis

All three parts are implemented: five-store analysis, tool-backed DeepSeek questions,
and AI dashboard controls. Live dashboard: https://task4.evgeniy.win

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

The key is only read server-side. Do not commit `task4.env`. The deployed key is
stored in `/root/itransition/task4/task4.env` with mode 600.

The input archive must be extracted into `data/STORE1/` through `data/STORE5/`.
Each directory contains `users.csv`, `orders.parquet`, and `books.yaml`.
Input data and detailed local outputs are excluded from Git; the generated
dashboard contains the aggregate results necessary to view the submission.

`analysis.py` is the reusable implementation. `update_notebook.py` refreshes the
explained steps from its function definitions while preserving the earlier
lessons. `run_notebook.py` executes every cell with the project's Jupyter kernel
and saves the outputs, without requiring an additional notebook runner.

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
totals. PNG charts are in `output/`. These detailed files stay local; the web
container only includes the generated aggregate dashboard, not the contact directory.

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

Then open http://127.0.0.1:8765. The tunnel service URL to publish is
**http://127.0.0.1:5002**. URL publication is left to the owner.

## AI questions and dashboard controls

`assistant.py` uses DeepSeek's tool-calling API. It receives table schemas and
bounded tool results, not a dump of the store databases. Tools are defined by
Pydantic schemas and execute validated pandas operations in `data_tools.py`.
The model cannot execute Python, SQL, filesystem operations, or arbitrary URLs.

For factual questions the model selects a plan; the displayed answer is rendered
from successful tool output. Free-form final model prose is not used for numerical
answers, preventing it from replacing computed figures with invented ones. Open
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
- "List all aliases and spending for user 48403 in Store 1."
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

For submission, provide the live dashboard URL and this code repository's URL.
The original input archive is intentionally excluded from Git; extract the supplied
assignment archive into `data/` to reproduce the full analysis.
