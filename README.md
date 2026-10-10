# Paribus · Hospital Bulk Processor

A FastAPI service that ingests a CSV of hospitals and creates them through the
[Hospital Directory API](https://hospital-directory.onrender.com/docs). It uses
batch IDs to make each upload atomic: rows are created inactive, and the batch is
activated only when every row succeeds.

## Quick start

```bash
docker compose up -d
open http://localhost:8000          # dashboard
open http://localhost:8000/docs     # API docs
```

```bash
curl -F file=@samples/valid_5.csv http://localhost:8000/hospitals/bulk
```

## API

| Method   | Path                                 | Purpose                                              |
| -------- | ------------------------------------ | ---------------------------------------------------- |
| `POST`   | `/hospitals/bulk`                    | Upload a CSV and process it (`?wait=false` → `202`)  |
| `POST`   | `/hospitals/bulk/validate`           | Dry-run validation, no upstream calls                |
| `GET`    | `/hospitals/bulk/{batch_id}`         | Progress and results (polling endpoint)              |
| `WS`     | `/hospitals/bulk/{batch_id}/ws`      | Live progress: a snapshot on every change, then closes |
| `POST`   | `/hospitals/bulk/{batch_id}/resume`  | Retry failed rows / activation (`?wait=false` → `202`) |
| `DELETE` | `/hospitals/bulk/{batch_id}`         | Roll back: delete the whole batch upstream           |
| `GET`    | `/health`                            | Liveness                                             |

### CSV format

```csv
name,address,phone
General Hospital,123 Main St,555-1234
Riverside Clinic,9 River Rd,
```

- `name` and `address` are required; `phone` is optional (column or value).
- Header is case-insensitive, any column order, BOM and surrounding whitespace tolerated.
- Blank lines are skipped. Max **20** rows, max 64 KB, UTF-8 only, `.csv` extension.
- Invalid files are rejected with `422` and a per-row error report **before** any upstream call.

### Response

```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "total_hospitals": 20,
  "processed_hospitals": 20,
  "failed_hospitals": 0,
  "processing_time_seconds": 5.57,
  "batch_activated": true,
  "error": null,
  "hospitals": [
    {
      "row": 1,
      "hospital_id": 101,
      "name": "General Hospital",
      "address": "123 Main St",
      "phone": "555-1234",
      "status": "created_and_activated",
      "error": null
    }
  ]
}
```

### Live progress

Upload with `?wait=false`, then connect to the WebSocket. The server sends the job
JSON (same shape as above) every time a row finishes, and closes the socket once
the batch reaches a final state. Connecting late still returns the final snapshot.

```bash
curl -F file=@samples/max_20.csv "localhost:8000/hospitals/bulk?wait=false"   # → batch_id
websocat ws://localhost:8000/hospitals/bulk/<batch_id>/ws
```

## Dashboard

`/dashboard` is a small UI for the same flows: validate, upload, watch rows update
live, resume, and roll back. It is server-rendered with Jinja templates and
[htmx](https://htmx.org). The server sends HTML fragments over a WebSocket and htmx
swaps them in, so the page carries almost no JavaScript. Styling is
[Pico.css](https://picocss.com) plus a few lines of CSS.

## Lifecycle

```
                    all rows created + activated
  upload ──► processing ─────────────────────────► completed ──┐
                 │                                              │
                 │ any row or activation fails                  │ DELETE
                 ▼                                              ▼
               failed ──── POST /resume ──► processing      rolled_back
                 │                                              ▲
                 └────────────────── DELETE ────────────────────┘
```

| Row status              | Meaning                                    |
| ----------------------- | ------------------------------------------ |
| `pending`               | Not attempted yet                          |
| `created`               | Exists upstream, inactive                  |
| `created_and_activated` | Batch activated                            |
| `failed`                | Create failed; see `error`                 |
| `rolled_back`           | Deleted upstream                           |

## Design decisions

The reasoning behind these, including gaps in the brief and known limitations, is in
[thoughts.md](thoughts.md).

**Batch atomicity.** A partially failed batch is never activated, so a half-imported
CSV never becomes visible. The client chooses what to do next: `resume` or roll back.

**Concurrency.** Rows are created in parallel over one pooled `httpx.AsyncClient`,
bounded by a process-wide semaphore (`PARIBUS_CONCURRENCY`, default 20). The
semaphore is shared across jobs, so concurrent uploads cannot flood the upstream.
The upstream takes ~5 s per create; measured live, 20 rows finish in **~5.6 s**
instead of ~100 s sequentially.

**Safe retries.** Transient failures (`429/502/503/504`, connection errors) are
retried with exponential backoff. `POST` is not idempotent, so it is retried only
when the request never reached the server (connect errors). A read timeout on a
create is **not** retried, because the hospital may already exist.

**Resume without duplicates.** Before retrying, `resume` lists the batch upstream
and adopts any record that matches a failed row. Those are creates that succeeded
but whose response was lost. Only truly missing rows are re-posted, then the batch
is activated. This was verified live by forcing 2 s timeouts against the ~5 s
upstream: all five rows "failed", resume adopted all five, and no duplicates were
created.

**Concurrency control.** A job must be claimed before any operation. Resuming a job
that is `processing` or `completed`, or acting on a `rolled_back` job, returns `409`.

**Sync by default, async on demand.** `POST /hospitals/bulk` returns the full
result, as the spec asks. `?wait=false` returns `202` immediately, and the client
polls `GET /hospitals/bulk/{batch_id}`.

**One change signal, two streams.** Each job exposes `updates()`, an async
iterator that wakes whenever a row or the job status changes. The JSON WebSocket
and the dashboard's HTML WebSocket both consume it; they differ only in how a
snapshot is encoded.

**Storage.** Jobs live in an in-memory `JobStore`, as the assignment allows. To
scale horizontally, swap it for Redis or Postgres and move execution to a worker
queue. The store is the only stateful seam.

## Project layout

```
app/
  main.py              app factory, lifespan (shared HTTP client), error handlers
  routes.py            JSON API endpoints + progress WebSocket
  dashboard.py         dashboard pages, fragments, HTML WebSocket
  streaming.py         shared "send each snapshot until finished" loop
  templates/           Jinja layout, macros, partials
  static/              CSS + a small error-flash script
  dependencies.py      DI: settings, store, processor, upload parsing, job lookup
  csv_parser.py        CSV → validated rows + error report
  directory_client.py  upstream client with retry policy
  processor.py         create → activate, resume, rollback
  schemas.py           models and state transitions
  store.py             in-memory job store
  config.py            env-driven settings
tests/                 unit + API tests, in-memory fake upstream, opt-in live test
samples/               CSVs covering every flow
```

## Configuration

| Env var                   | Default                                   |
| ------------------------- | ----------------------------------------- |
| `PARIBUS_DIRECTORY_URL`   | `https://hospital-directory.onrender.com` |
| `PARIBUS_CONCURRENCY`     | `20`                                      |
| `PARIBUS_REQUEST_TIMEOUT` | `30` (seconds)                            |
| `PARIBUS_MAX_ATTEMPTS`    | `3`                                       |
| `PARIBUS_BACKOFF_BASE`    | `0.5` (seconds)                           |
| `PARIBUS_MAX_ROWS`        | `20`                                      |
| `PARIBUS_MAX_FILE_BYTES`  | `65536`                                   |

## Sample CSVs

| File                         | Expected                                        |
| ---------------------------- | ----------------------------------------------- |
| `valid_5.csv`                | `200`, completed (includes quoted commas, blank phone) |
| `max_20.csv`                 | `200`, completed at the 20-row limit            |
| `messy_but_valid.csv`        | `200`, completed (BOM, odd casing, reordered columns, blank lines) |
| `no_phone_column.csv`        | `200`, completed                                |
| `invalid_rows.csv`           | `422`, errors on rows 2, 3, 4                   |
| `too_many_21.csv`            | `422`, over the row limit                       |
| `missing_address_column.csv` | `422`, missing required column                  |
| `unknown_column.csv`         | `422`, unknown column                           |
| `header_only.csv`            | `422`, no rows                                  |
| `empty.csv`                  | `422`, empty file                               |
| `not_a_csv.txt`              | `415`, wrong file type                          |

To try the failure and resume path against the real upstream, force timeouts:

```bash
PARIBUS_REQUEST_TIMEOUT=2 docker compose up -d
curl -F file=@samples/valid_5.csv localhost:8000/hospitals/bulk          # status: failed
curl -X POST localhost:8000/hospitals/bulk/<batch_id>/resume             # status: completed
docker compose up -d                                                     # back to normal
```

## Tests

```bash
uv sync
uv run pytest                          # 96 tests, upstream faked in memory
PARIBUS_LIVE=1 uv run pytest           # + end-to-end against the real upstream
uv run pytest --cov=app                # 100% line coverage
```

The suite covers CSV edge cases, the retry policy (what is and isn't retried),
concurrency limits, partial failure, activation failure, resume (including orphan
adoption), rollback, state conflicts, async mode, live progress streaming, both
WebSockets, every dashboard flow, and every sample file.

## Deploy (Render)

`render.yaml` is a Docker blueprint. In Render, choose **New → Blueprint**, point it
at this repo, and deploy. The container honours Render's `$PORT`.
