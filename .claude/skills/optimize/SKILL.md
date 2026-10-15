---
name: optimize
description: Performance and cost optimization checklist for LegacyLens (ingestion, embeddings, search, LLM calls, graph, frontend). Use after a feature works end to end, or when something is slow, rate-limited (429), or expensive.
---

# Optimize (measure first, then fix)

Never optimize blind: time the step (`time.perf_counter`, request logs) and fix the biggest cost only.

## LLM / embeddings (tightest limits: embeddings 10K TPM, gpt-4o 30K TPM)
- Batch embedding inputs (up to 2,048 per request); retry 429 using the `Retry-After` header, with backoff and jitter.
- Cache embeddings and LLM extraction results by content hash in `data/.state/` so re-ingest only processes changed artifacts.
- Embed once per chunk; dedupe identical chunks; keep chunks at 900/150 chars unless measured otherwise.
- Cap `max_tokens`, send only the retrieved evidence a prompt needs, and use `temperature` 0.2 for extraction.
- Run independent LLM calls concurrently (bounded with a semaphore) while respecting TPM limits.

## Search
- Upload documents in batches; create the index once; use filters (artifact_type, author) instead of post-filtering in Python.
- Request only needed fields (`select`) and a sensible `top_k`. Reuse the client instance.

## Graph / analyzer
- Build the NetworkX graph once and keep it in memory; persist to `graph.json` only after ingestion or approval.
- Precompute risk scores at ingest/approval time and cache; `GET /risks` should just read.
- Avoid O(n²) scans over artifacts; index by entity id.

## API
- Reuse HTTP/Azure clients (app lifespan), not per request. Gzip large responses. Paginate big lists.
- Cache the demo-critical responses so the live demo can't fail on a slow upstream.

## Frontend
- Fetch in parallel, memoize derived data, avoid rerender storms, lazy-load graph visualization.

## Report
After optimizing, state the before/after numbers (time, tokens, calls) in one or two lines.
