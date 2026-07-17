# Lumen RAG service

The service exposes synchronous chunk/embed/store APIs and an asynchronous
`/api/parse-document` pipeline backed by Redis.

## Durable parse admission and recovery

Backend derives a stable idempotency key from the normalized document UUID and
the exact Markdown SHA-256. Redis atomically returns the active/completed prior
task or admits one new task, closing the window where RAG accepted work but the
Backend process exited before storing the returned task ID.

Workers acquire a unique processing lease rather than a bare task ID. Only the
current lease token may heartbeat, complete, fail, cancel, or requeue the task.
Expired leases are recovered on startup and periodically while workers run;
missing payloads become terminal failures instead of retry loops. Graceful
shutdown requeues leases still owned by that process. Configure the cadence with
`TASK_VISIBILITY_TIMEOUT_SECONDS`, `TASK_HEARTBEAT_INTERVAL_SECONDS`, and
`TASK_STALE_RECOVERY_INTERVAL_SECONDS`; heartbeat must be shorter than the
visibility timeout.

`TEMP_DIR/task_payloads` is durable task state, not disposable source-tree
scratch space. Compose maps `TEMP_DIR` and `RAG_CACHE_DIR` into
`RAG_TASK_STATE_DIR`, mounts the RAG source read-only, and keeps tokenizer caches
in that writable state directory.

## Shared retrieval package

Retrieval, query adaptation, and Elasticsearch connection behavior are owned by
`shared/python/recall_lib`. Both the business backend and this RAG service import
that package; the RAG service no longer carries a second algorithm copy. The
Compose service mounts `shared/python` read-only and declares it in `PYTHONPATH`.

## Asynchronous worker credentials

Queued task metadata never contains credentials. Configure external-service
credentials on the RAG worker process instead:

| Setting | Purpose |
| --- | --- |
| `EMBEDDING_API_KEY` | embedding provider credential |
| `EMBEDDING_BASE_URL` | worker embedding endpoint override |
| `CV_API_KEY` | visual parser provider credential |
| `CV_BASE_URL` | worker visual parser endpoint override |
| `ES_USERNAME` / `ES_PASSWORD` | authenticated Elasticsearch connection |
| `ES_HOST` | worker Elasticsearch endpoint |

All direct embedding, rerank, and visual-parser HTTP calls use the same bounded
transport. It ignores environment proxy settings, rejects redirects, and caps
the decompressed response body. Configure its operational limits with
`RAG_PROVIDER_CONNECT_TIMEOUT_SECONDS` (default 10, hard maximum 60),
`RAG_PROVIDER_READ_TIMEOUT_SECONDS` (default 120, hard maximum 600), and
`RAG_PROVIDER_MAX_RESPONSE_BYTES` (default 16777216, hard maximum 268435456).
Invalid, non-positive, or over-limit values fall back to the documented
defaults.

In Compose these values come from the explicit RAG allowlist in `docker/.env`.
The RAG container does not inherit `backend/.env`, so it cannot read business
database, JWT, MinIO root, SMTP, Gateway, or model-encryption credentials.

The asynchronous endpoint still accepts the legacy secret form fields so old
clients receive an explicit configuration error, but it never copies those
values into the Redis task. Existing queued tasks that contain legacy secret
fields can be restored once; current worker settings take precedence and the
stored metadata is rewritten without secrets.

## Tests

Install development dependencies and run the focused unit suite:

```bash
python3 -m pip install -r services/rag/requirements-dev.txt
services/rag/scripts/test.sh
```

The suite covers queue ordering, idempotent admission, lease fencing and stale
recovery, task metadata redaction, old-task compatibility, `ir-table`
asynchronous argument forwarding, internal authentication, writable cache
placement, and canonical `recall_lib` ownership/path resolution.
