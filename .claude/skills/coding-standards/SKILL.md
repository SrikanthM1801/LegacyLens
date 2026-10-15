---
name: coding-standards
description: LegacyLens coding best practices for Python/FastAPI backend and React/TypeScript frontend. Use whenever writing or reviewing code in this repo.
---

# Coding standards (hackathon-fast, still clean)

## Python / FastAPI
- Type-hint everything; `from __future__ import annotations`; Pydantic v2 models from `app/models.py` at every boundary. Never invent local shapes.
- Routers are thin: validate, call a service/agent, return a model. Business logic lives in `services/`, `ingestion/`, `graph/`, `agents/`.
- Use `Depends` for settings/services so tests can swap in mocks. Settings come from `get_settings()`; no `os.environ` reads elsewhere.
- Real and mock services share one interface (Protocol/ABC). Selection happens in one factory keyed on `settings.use_mocks`.
- I/O-bound code (Azure calls) is `async` or run in a threadpool; never block the event loop. Set timeouts on every network call.
- Raise `HTTPException` with clear status codes (404 unknown id, 422 bad input, 502 upstream Azure failure). No bare `except:`; log with `logging`, not `print`.
- LLM calls: JSON mode, validate with Pydantic, retry once, fall back to mock. Retry 429 with exponential backoff + jitter.
- All prompts in `agents/prompts.py`. No secrets in code, logs, or error messages.
- Pure functions for scoring/chunking/redaction so they're trivially unit-testable.
- Deterministic code paths (fixed seeds, sorted iteration) so demo and tests are stable.

## React / TypeScript
- `strict` TS; types mirror `models.py`; one typed API client module, no `fetch` scattered in components.
- Handle loading, empty, and error states on every page. Show citations next to every generated claim.
- Accessible by default: labels, contrast, keyboard focus. Responsive layout.

## General
- Small functions, clear names, comments explain *why*. Match surrounding style and docstring density.
- No dead code, no speculative abstractions beyond `GraphStore` and the mock/real split.
- Every endpoint touched gets a smoke test in mock mode; `pytest` must pass before commit.
- Validate and sanitize all external input; redact PII before indexing; never log raw artifact bodies.
- Commit small, with clear messages, after each working step.
