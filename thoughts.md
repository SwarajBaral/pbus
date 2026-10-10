# Thoughts

Notes on how the brief was read, what the upstream API actually does, and the
trade-offs behind the design. The README covers *how* to use the service; this
file covers *why* it looks the way it does.

## Gaps in the brief, and how they were filled

| Question the brief leaves open | Decision |
| --- | --- |
| What happens if some rows fail? | The batch is never activated. The job ends `failed` and can be **resumed** (retry only what failed) or **rolled back** (delete the batch upstream). A half-imported CSV never becomes visible. |
| The example response shows 25 hospitals, but the limit is 20 | The limit wins: a 21-row file is rejected before any upstream call. |
| A full result in the response vs. progress tracking | Both. The default is synchronous and returns the full result, as the example shows. `?wait=false` returns `202` and progress is available by polling or over a WebSocket. |
| Is the upstream reliable? | Not stated, so it was probed (below) and treated as slow and occasionally unavailable. |
| Header row? Extra columns? | The header is required, matched case-insensitively and in any order. Unknown columns are rejected instead of silently ignored, so a typo like `adress` fails loudly. |
| Validate row by row as we go, or up front? | Up front. A bad file costs zero upstream calls, and every problem is reported in one response. |

## What the upstream API actually does

Probed by hand before writing the client:

- A create takes **about 5 seconds**. The first call after idle can take about 30 seconds (Render free-tier cold start).
- `GET`, `PATCH /activate` and `DELETE` on an empty batch return **404**, not an empty result. The client treats 404 as "nothing there" for list and delete, but as an error for activate.
- Hospital IDs restart from 1 when the service restarts, so its storage is in memory too.
- `422` comes back for empty `name` or `address`. Our own validation catches these first.

## Key decisions

**Concurrency over speed tricks.** About 5 seconds per create makes sequential
processing about 100 seconds for 20 rows. Creating rows concurrently, behind one
process-wide semaphore, brings that to about 5.5 seconds. Because the semaphore is
shared, two uploads at once still put at most 20 requests on the upstream.

**Retries that cannot create duplicates.** Each upstream call states its own
policy (`retry_on_timeout`). Reads, activate and delete are safe to repeat, so
they retry on any transport error. A create is retried only when the request
provably never reached the server (connection errors). A timed-out create may
already exist, so it is marked failed instead of being retried blindly.

**Resume reconciles before it retries.** Resume lists the batch upstream once and
adopts any record matching a failed row on `(name, address, phone)`. Each orphan
can be claimed only once, so two identical CSV rows still map to two records.
False matches are not possible because the batch ID is ours and brand new. This
was checked against the real API by forcing every create to time out: resume
adopted all five rows and created no duplicates.

**One change signal, two streams.** A job wakes its watchers whenever a row or the
job status changes. The JSON WebSocket and the dashboard's HTML WebSocket consume
the same iterator and differ only in how they encode a snapshot.

**Server-rendered dashboard.** Jinja templates plus htmx instead of a JavaScript
app. One template renders the batch panel for the first load, row clicks and live
updates, which keeps the UI to a handful of small files with almost no JavaScript.

## Known limitations

- **Single process.** The job store and the concurrency limit live in memory, so
  running several workers would split state between them.
- **Restarts lose jobs.** Render's free tier sleeps after 15 minutes idle. A job
  that is still running at shutdown is lost, and its rows stay inactive upstream.
- **Jobs are never evicted.** That's fine at this scale; long-running, it needs a TTL.
- **No auth or rate limiting** on this service's own endpoints.
- **The activate count isn't checked.** The upstream returns how many rows it
  activated; that number is not compared with the expected count.
- `processing_time_seconds` measures the most recent operation (run, resume or
  rollback), not the total over the job's life.

## If this went to production

1. Move jobs to Redis or Postgres and execution to a worker queue (arq or Celery),
   so jobs survive restarts and workers scale horizontally.
2. Accept an idempotency key on upload so a client retry doesn't start a second batch.
3. Ask the upstream for idempotency keys on create. That would make retrying a
   timed-out create safe and remove the need for reconciliation.
4. Add metrics and tracing on upstream latency, retries and failure rates.
5. Add authentication, per-client rate limits, and a TTL on stored jobs.
