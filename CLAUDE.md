# LegacyLens — Claude Code Context

Hackathon project (Microsoft Hackathon 2026, track: Knowledge Management & Preservation). Started 14 Oct 2026. **Speed and a working end-to-end demo beat completeness.** Full design is in `Plan.md`; Azure setup in `AzureResources.md`. Read `Plan.md` sections 11–13 before building agents, the analyzer or the dataset.

## What we're building
An AI agent that finds knowledge held by one person (low bus factor, no docs), interviews the right person with evidence-grounded questions, then publishes cited runbooks/handover packs and answers questions via a Project Copilot. Loop: **Observe → Detect risk → Interview → Publish**, and answers feed back so the risk score drops (red → green in the dashboard).

## Non-negotiables
1. **`USE_MOCKS=true` must always work.** Every external call (LLM, embeddings, search) has a mock with the same interface. Whole app and all tests run offline with no keys.
2. **`backend/app/models.py` is the contract.** Only add optional fields. Don't rename or remove without telling the user and updating `Plan.md`.
3. **Every user-facing claim carries a `Citation`.** No citation, no claim. If context is insufficient, say so and suggest an interview.
4. **All LLM prompts live in `backend/app/agents/prompts.py`.** Nowhere else.
5. **Never read, print or commit `.env`.** It holds real Azure keys. Use `.env.example` for reference.
6. **Demo data is sacred.** The 3 planted risks (payment gateway outages / Ravi, settlement batch job / Arjun, Kafka-vs-RabbitMQ decision / Priya) must always rank in the top 3. Keep an acceptance test for it.
7. Risk scores describe **knowledge concentration, not individual productivity.** UI shows topics, not employee rankings. The one person-level evaluation is the **onboarding assessment**, and only with its safeguards: assigned by a team owner, the candidate is told up front what is evaluated, sees the result before the channel does, scoring is against cited project documents (never style or fluency), and the card says an owner should review it. No leaderboards.

## Always research first
**Search the web before touching anything external** — Azure OpenAI, AI Search, Graph, Copilot Studio, M365 Agents SDK, any pip/npm package, API versions, model names, errors you can't explain. Use WebSearch (extended for niche/recent topics) and read official pages (learn.microsoft.com, library GitHub/PyPI/npm) with WebFetch. Pinned versions in `requirements.txt` may be stale; flag conflicts instead of silently upgrading. Never put secrets or real data in queries. Details: `.claude/skills/research-first`.

## Working one task at a time
The whole build is split into small tasks in `TASKS.md` (T00–T38, with status checkboxes). Use `/next-task` (or `/next-task T07`) to complete one end to end, and `/task-status` for progress. Finish and verify one task before starting the next; keep `TASKS.md` statuses accurate. This build order supersedes the coarse list below.

## Project skills (`.claude/skills/`)
| Skill | Use when |
| --- | --- |
| `next-task` | Starting or continuing the next item in `TASKS.md` |
| `task-status` | Asking for progress / what's left |
| `research-first` | Any task involving an external library/service/API or unfamiliar error |
| `coding-standards` | Writing or reviewing any code |
| `optimize` | A feature works end to end and is slow, rate-limited, or costly |
| `new-endpoint` | Adding or changing an API endpoint |
| `demo-check` | Before merging, before rehearsal, after changing data/ingestion/analyzer/agents |

Built-in skills also worth using: `/simplify` after a feature, `/code-review` before merging, `/security-review` before the demo, `/claude-api` when writing LLM calls.

## Best practices and optimization
Follow `coding-standards` always: typed Pydantic contracts at every boundary, thin routers, dependency-injected services, timeouts and retries on network calls, no secrets in logs, deterministic code paths, tests for touched endpoints. Optimize only after measuring (`optimize` skill): batch and cache embeddings, retry 429 with backoff, precompute risk scores, reuse clients.

## Stack
- Backend: Python 3.11+, FastAPI, Pydantic v2, pydantic-settings, uvicorn, pytest + httpx TestClient
- LLM: Azure OpenAI (`openai` SDK, `AzureOpenAI`), deployments `gpt-4o` and `text-embedding-3-small` (1536-dim; never use 3-large)
- Search: Azure AI Search (`legacylens-chunks`, index created by code, not by hand), hybrid vector + keyword
- Graph: NetworkX behind a `GraphStore` interface, persisted to `data/graph.json` (gitignored)
- Frontend: React + Vite + TypeScript. Two pages first: Risks, Ask. Then Interview.
- Teams: thin bot calling `POST /ask`; Copilot Studio preferred if time-limited.

## Repo map (target)
```
backend/app/{main,config,models}.py
backend/app/api/         routers: ingest, risks, interviews, artifacts, ask, graph, people
backend/app/services/    llm.py, search.py   (real + mock, same interface)
backend/app/ingestion/   loaders, normalizer, redactor, chunker, extractor, pipeline
backend/app/graph/       GraphStore + NetworkX impl
backend/app/agents/      prompts.py, gap_analyzer.py, interviewer.py, generator.py, copilot.py
backend/tests/           smoke tests in mock mode
scripts/                 generate_dataset.py, ingest.py
data/sample/             committed synthetic JSON
frontend/  bot/
```
Currently built: `config.py`, `models.py`, `requirements.txt`. Everything else is a stub. Check the filesystem for current status before assuming.

## Build order (walking skeleton first)
1. `main.py` with `/health` + CORS + one pytest. Reconcile models vs plan (below).
2. `services/llm.py` + `services/search.py`: mock first (deterministic, in-memory), then real Azure behind the same interface, plus a connectivity smoke script.
3. `scripts/generate_dataset.py`: deterministic, fixed seed, templated, no LLM. ~60 emails/chats, ~20 tickets, ~6 wiki docs, ~40 commits, 4 people (Ravi departing, Priya, Arjun, Meera). Planted risks plus well-documented contrast topics (release process, onboarding, API auth).
4. Ingestion pipeline: load → normalize → redact PII → chunk (900/150 chars) → embed → extract entities/relations → upsert to search + graph. `POST /ingest`, `GET /graph`.
5. Gap analyzer + `GET /risks`, `/risks/{id}`, `/people`. Acceptance test: planted risks in top 3.
6. Interview agent (`/interviews/*`): 3–5 grounded questions, one at a time, review/approve, re-ingest answers as `Artifact(type=interview)`, recompute risk.
7. Generator (`/artifacts/runbook`, `/artifacts/handover`) and Copilot (`/ask`).
8. Frontend (Risks → Ask → Interview, risk drops after approval), then Teams bot.
9. Stretch only after the demo flow works: decision log, voice, live Graph ingestion, graph visualization.

**Do the demo path first:** risks list → start interview → answer → approve → risk goes red→green → generate runbook with citations → ask Copilot. Cut anything that doesn't serve this.

## Known doc/code drift to resolve (ask the user which side wins; default: update the plan, keep code)
- `Plan.md` §9 uses `risk_level`, `topic_name`, `author_id`, `is_departing`, status `open|completed`; `models.py` has `topic`, `author`, no `risk_level`, no `Person.is_departing`, status `pending|in_progress|completed`.
- Risk boost for departing owners and "interview the departing person first" need `Person.is_departing` (+ optional `departure_date`). Add as optional fields. Add a derived `risk_level` (high ≥ 0.6, medium ≥ 0.3, else low) as an optional field or computed in the API.
- `DOC_ARTIFACT_TYPES` counts only `doc`; approved interviews should also raise coverage (Plan §11.2). Include `interview` once approved.
- Plan env var `AZURE_SEARCH_INDEX` vs code `AZURE_SEARCH_INDEX_NAME` (code is right).
- `Plan.md` lists `CLAUDE.md` and `docs/PROJECT_PLAN.md`; plan actually lives at repo root as `Plan.md`.
- `config.data_dir` defaults to `data/sample`; `.env.example` plan says `DATA_DIR=./data`. Keep `data/sample` as the ingestion source.

## Core algorithms (summary; details in Plan.md §11)
- **Activity weights:** RESOLVED 5, DECIDED 4, COMMITTED_TO 3, authored doc 2, mention 1; recency boost.
- **Bus factor:** distinct people with ≥15% share of weighted activity; one person ≥70% = single-person risk.
- **Coverage (0–1):** search hits restricted to doc/interview artifacts + `DOCUMENTED_BY` edges.
- **Risk:** `activity × (1/max(bus_factor,1)) × (1 − coverage)`, ×1.5 if top owner departing.
- **Interview questions** must quote concrete evidence and target tacit knowledge: triggers, first checks, decision criteria, workarounds, gotchas, escalation.
- **Copilot:** answer only from retrieved context + graph expansion; cite every claim.

## Engineering rules
- Simple and readable. No auth, no multi-tenancy, no abstractions beyond `GraphStore` and the mock/real service split.
- API shapes follow `Plan.md` §12; changing one needs the user's OK.
- LLM JSON output: use JSON mode, validate with Pydantic, retry once, fall back to mock.
- Embedding limit is 10K TPM: batch inputs and retry on HTTP 429.
- Mock LLM must return plausible, deterministic content so the demo works offline and tests are stable.
- Match surrounding code style; module docstrings explain purpose (see `config.py`, `models.py`).
- Add or update a smoke test for any endpoint you touch.

## Commands
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env                         # keep USE_MOCKS=true
cd backend && uvicorn app.main:app --reload  # API on :8000
cd backend && pytest                         # must pass before every commit
python scripts/generate_dataset.py           # regenerate data/sample
python scripts/ingest.py                     # CLI ingestion (same path as POST /ingest)
cd frontend && npm install && npm run dev    # UI on :5173
```
(Adjust if layout changes; verify a command works before documenting it as fact.)

## Workflow
- One branch per module, small PRs into `main`; `main` must always run in mock mode.
- After each change: run `pytest`, start the app, hit the endpoint. Fix what breaks.
- Commit after each completed step with a clear message.
- Set `USE_MOCKS=false` only for local real-Azure checks, then set it back to `true`.
- Risk mitigation: keep cached responses and a recorded backup video for the live demo.

## Azure facts
Resource group `rg-legacylens`; OpenAI `legacylens-aoai` (eastus2): `gpt-4o` 30K TPM, `text-embedding-3-small` 10K TPM; Search `legacylens-search` (eastus, Basic, ~$0.10/h, can't pause). Teardown after the event: `az group delete --name rg-legacylens --yes`.

## Pitch points to preserve in the product
Consent-scoped ingestion, PII redaction step, human approval before publishing, citations everywhere, no performance surveillance, measurable outcome (knowledge risk score falls after interviews).
