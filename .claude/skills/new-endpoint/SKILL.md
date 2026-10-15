---
name: new-endpoint
description: Step-by-step recipe for adding or changing a FastAPI endpoint in LegacyLens so it matches Plan.md section 12, models.py contracts, mock parity, and tests.
---

# Add an endpoint

1. Look up the endpoint in `Plan.md` §12 and the request/response models in `backend/app/models.py`. Reuse them; if a field is missing, add an **optional** one and tell the user.
2. Search the web (see `research-first`) if it touches an external SDK.
3. Put logic in `services/`, `ingestion/`, `graph/` or `agents/`; create a router in `backend/app/api/<name>.py` that only validates, delegates, and returns the model. Register it in `main.py`.
4. Ensure every external call has a mock with the same interface and works under `USE_MOCKS=true`.
5. Responses that make claims include `Citation`s.
6. Add a smoke test in `backend/tests/` using FastAPI `TestClient` in mock mode (happy path + one error case).
7. Run `cd backend && pytest`, start the app, and hit the endpoint (curl or `/docs`). Fix what breaks.
8. Update `Plan.md` §12 only if the shape changed. Commit with a clear message.
