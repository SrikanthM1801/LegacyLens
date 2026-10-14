# frontend/

Minimal React + Vite + TypeScript client. Scaffolded in **step 6** — two pages only:

- **Risks** — table of `GET /risks` (topic, owner candidates, bus factor, coverage, risk score)
- **Ask** — a box that posts to `POST /ask` and renders the answer with its citations

It talks to the backend at `VITE_API_BASE_URL` (default `http://localhost:8000`).
Types mirror `backend/app/models.py` — that file stays the source of truth.
