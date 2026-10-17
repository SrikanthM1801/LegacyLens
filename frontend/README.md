# frontend/ — LegacyLens transparency dashboard

React + Vite + TypeScript. It shows, at **org → client account → project** level, what LegacyLens is
allowed to read, what it stored and where, what it answered and what failed. It is built to run as a
Teams static tab (T31f) and also in a plain browser for development.

It reads the backend's `/dashboard/*` API, which aggregates the audit trail (Azure Blob) and the consent
register (Azure Tables). Types in `src/types.ts` mirror `backend/app/models.py` (section 11), which stays
the source of truth.

## Run it
```bash
# backend, with generated demo data (offline, no Azure, no admin key)
cd backend && DASHBOARD_DEMO=true LIVE_M365=false USE_MOCKS=true ADMIN_API_KEY= ../.venv/bin/uvicorn app.main:app --port 8000
# frontend
cd frontend && npm install && npm run dev        # http://localhost:5173
npm test                                          # vitest: formatting, every chart, the app flow
npm run build                                     # type-check + production bundle (about 55 kB gzipped)
```
`VITE_API_BASE_URL` (default `http://localhost:8000`) points at the API. Against a real deployment
(`LIVE_M365=true`) the API requires `ADMIN_API_KEY`: the page asks for it once, keeps it in
`sessionStorage` for that tab only, and sends it as `X-Admin-Key`.

URL options: `?tab=overview|consent|audit` opens a section, `?theme=light|dark` forces a theme
(a Teams tab can pass its own).

## Host it through the dev tunnel (one URL for the API and the UI)
The backend serves the built dashboard at **`/app`** whenever `frontend/dist` exists, so the tunnel that already
carries the bot also carries the dashboard, with no second tunnel and no CORS.
```bash
cd frontend && npm install && npm run build        # on the machine that runs the backend (dist/ is not in git)
cd ../backend && ../.venv/bin/uvicorn app.main:app --port 8000
devtunnel host legacylens                           # your existing tunnel
# open  <PUBLIC_BASE_URL>/app/      e.g. https://<id>-8000.inc1.devtunnels.ms/app/
```
A dev tunnel is not hosting: it forwards a port from the machine running it, so the dashboard is up only while
that machine, `uvicorn` and the tunnel are. The page holds no data; every number needs the `ADMIN_API_KEY` of
*that* backend (the key typed in the browser is the key in the host machine's `.env`).
Note: the bot's tunnel is created anonymous (`-a`) because Teams and Graph must reach it. That also exposes
every other endpoint of the API, so to look at the dashboard on your own, prefer a private tunnel (no `-a`).

## Where the numbers come from
Only the **audit trail** (Azure Blob) and the **consent register** (Azure Tables). Nothing on the page is read
from the knowledge base, the sample dataset or a mock. The badge next to the title always says which:
- **Live: Azure audit trail** (`LIVE_M365=true`): the real blobs and tables. In live mode the API refuses to answer
  (503, with the reason) if either is not configured or cannot be read; it never falls back to memory or demo data.
  Sign in first (`az login`) so the backend can read the storage account.
- **Offline: this session only** (`LIVE_M365=false`): events recorded by the running server, in memory.
- **Demo data** (`DASHBOARD_DEMO=true`, offline only): generated, clearly labelled.

## What is on the page
- **Overview:** the hero "conversations allowed" with a consent meter, stat tiles, messages and questions
  per day, where it is learning, what it did, what was redacted, consent decisions, knowledge risk before and
  after interviews (org level), recent failures.
- **Consent and data:** what is processed and where it is kept, and the consent register (the one place a
  person is named: who clicked Allow).
- **Audit trail:** the events, filterable. People appear as a role, never a name; no message text anywhere.

## Design rules (from the data-viz method)
- Marks: 2px lines, bars at most 24px with a 4px rounded data end, hairline solid grid, 2px surface ring on dots.
- Colour: the validated default palette (light and dark are separate selected steps). Blue = messages,
  orange = questions, one hue for nominal bars, status colours only for status and always with an icon + label.
  Aqua, yellow and magenta are not used for marks (they sit under 3:1 on the light surface).
- Every chart has a hover/keyboard tooltip, a legend where it has two or more series, and a table twin
  ("Show table"). Names are rendered as text, never as HTML. One filter row above everything; while data
  reloads the previous render stays at reduced opacity.
