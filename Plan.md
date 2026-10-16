# LegacyLens — Project Plan

> **Track:** Knowledge Management & Preservation (Microsoft Hackathon)
> **Status:** Day 4 — backend complete and green (T00–T25, 212 tests in mock mode). **Direction changed 17 Oct 2026: LegacyLens is a Microsoft Teams app fed by real Microsoft 365 data — see §21 Decision log.**
> **Hackathon dates:** started 14 Oct 2026; end date **TBD — fill this in**.
> **Purpose of this doc:** Single source of truth for what LegacyLens is, how it is built, and how the team works on it. Claude Code and every teammate should read this before changing code.

---

## 1. One-line summary

LegacyLens is a **Microsoft Teams app** that continuously captures an entire project's knowledge from the places the team already works — Teams channels and chats, a shared project mailbox, the project's SharePoint library, tickets and code — identifies undocumented areas and single-person knowledge risks, and interviews the right person **inside Teams** with evidence-grounded questions. It builds a living project knowledge graph and auto-generates cited runbooks, decision logs and handover packs, so no knowledge is lost when people leave, rotate, or hand over the project.

**Where it lives:** a Teams bot (ask it questions in the project channel, answer interviews in 1:1 chat) plus a risk dashboard as a Teams tab. There is no standalone website — the backend stays API-first and Teams is the surface.

---

## 2. Problem

- When a senior engineer, architect, or support lead leaves, rotates, or hands over a project, years of know-how leave with them.
- Handover documents are rushed, incomplete, and rarely read again.
- Critical knowledge is **tacit**: it lives in people's heads, in scattered chat threads, and in ticket comments, never in a wiki.
- Teams don't know *which* knowledge is at risk until it's already gone (e.g. "only Ravi knows how to fix the payment gateway").
- For a services company like Presidio, frequent project handovers and attrition make this a recurring, costly problem, both internally and for clients.

## 3. Solution

LegacyLens runs one continuous loop per project:

```
 OBSERVE ──► DETECT RISK ──► INTERVIEW ──► PUBLISH
    ▲                                        │
    └────── answers feed back into KB ◄──────┘
```

1. **Observe:** Ingest the project's real Microsoft 365 activity through Microsoft Graph and the Teams bot — channel and group-chat messages (per-conversation consent), a shared project mailbox, the project SharePoint document library — plus tickets and commits. New messages flow in **in real time**; a scheduled job backfills history and catches up on anything missed. The synthetic dataset stays as the project's "past 6 months" so a fresh tenant still has a history to analyse (§13, §21).
2. **Detect risk:** Build a knowledge graph of people, systems, incidents, and decisions. Score each topic for *bus factor* (how many people know it) and *documentation coverage*. Surface single-person knowledge risks and undocumented areas.
3. **Interview:** For each high-risk topic, the bot opens a **1:1 Teams chat** with the right person and asks targeted, evidence-grounded questions as Adaptive Cards, one at a time (e.g. "In INC-142 and INC-188 you restarted the settlement worker. What told you that was the fix?"). The person reviews and approves before anything is published.
4. **Publish:** Turn answers into runbooks, decision logs, and handover packs, all with citations. Answers are ingested back into the knowledge base, so risk scores drop on the Teams tab and the Project Copilot — `@LegacyLens` in the channel — gets smarter.

### What makes it different
- Most projects in this track build "chat over documents." LegacyLens captures knowledge that **was never written down**, and it finds the gaps itself.
- Every answer and generated document is **cited** back to source artifacts or interview answers.
- A measurable outcome: the **knowledge risk score** of a project goes down over time.

---

## 4. Users / personas

| Persona | Need | How LegacyLens helps |
|---|---|---|
| **Departing / rotating expert** (e.g. Ravi, senior backend) | Hand over efficiently without writing a 40-page doc | Short, targeted interviews in Teams; review and approve captured knowledge |
| **Project / delivery manager** | Know where knowledge risk is before someone leaves | Risk dashboard, bus-factor heatmap, handover pack generation |
| **New joiner / successor** (e.g. Meera) | Get productive fast | Project Copilot: "How did Ravi usually handle X?" with sources |
| **Client (services scenario)** | Smooth engagement transition | Auto-generated handover pack at engagement end |

---

## 5. Goals and non-goals (hackathon scope)

### Done (backend, T00–T25)
- G1: Ingest a project dataset (emails, chats, tickets, docs, commits) into search + knowledge graph. ✅
- G2: Compute and rank knowledge risks per topic (bus factor × coverage × activity). ✅
- G3: Run an AI interview for a risk with grounded questions; store answers back into the KB. ✅
- G4: Generate a **runbook** and a **handover pack** with citations. ✅
- G5: Project Copilot Q&A with citations over `POST /ask`. ✅

### Goals (MVP, after the 17 Oct pivot)
- **G6 — Bot in Teams:** the app installs into a project channel; `@LegacyLens <question>` answers from `POST /ask` with citations rendered in the chat.
- **G7 — Consent + live capture:** on install the bot posts a consent card; on *Allow* it backfills recent history and every new message in that channel flows through the existing ingestion pipeline in real time. `@LegacyLens pause` revokes consent and stops ingestion.
- **G8 — Interviews in Teams:** the bot runs an interview in 1:1 chat as Adaptive Cards, one question at a time, then review & approve; approval recomputes the risk.
- **G9 — Dashboard as a Teams tab:** the React risk dashboard runs as a static Teams tab and visibly turns the topic red → green after approval.
- **G10 — Hybrid demo:** synthetic history + a live message posted during the demo, both cited in the same answer.

### Stretch (in priority order)
- Shared project mailbox connector (Outlook) with an LLM relevance filter, and the SharePoint project-library connector. **Cut first if time is short.**
- Decision log generation (T24).
- Auto-detecting newly created group chats tenant-wide (admin-enabled only — see §15).
- Voice interviews via Azure AI Speech.
- Deployment to Azure App Service / Container Apps; knowledge graph visualization.

### Non-goals (for the hackathon)
- A standalone public web app. The React dashboard ships **only** as a Teams tab; running it on `localhost:5173` stays a development convenience.
- Tenant-wide message watching, or ingesting anyone's personal mailbox. Scope is the project channel, the shared project mailbox and the project SharePoint site.
- Per-user delegated mailbox ingestion (roadmap, §18).
- Production auth, multi-tenancy, RBAC.
- Full PII/DLP pipeline (we describe it and implement a basic redaction step).

---

## 6. Architecture

```
┌───────────────────────── MICROSOFT 365 (real) ──────────────────────────┐
│ Teams channel + group chats │ Shared project mailbox │ SharePoint site  │
│   (bot activities, RSC)     │  (Graph, RBAC-scoped)  │ (Sites.Selected) │
└──────────┬──────────────────────────┬──────────────────────┬────────────┘
   bot activities          Graph change notifications    drive delta
           │                          │                      │
           ▼                          ▼                      ▼
┌──────────────────── INGESTION ADAPTERS (new) ──────────────────────────┐
│ app/bot/handlers   │  api/webhooks.py  │  ingestion/graph_loaders.py   │
│ consent gate ──► only consented chats/channels reach the pipeline      │
│ + ingestion/loaders.py (synthetic JSON = the project's "past history") │
└───────────────────────────────┬────────────────────────────────────────┘
                                ▼  everything becomes an Artifact
┌─────────────────────── INGESTION PIPELINE (unchanged) ─────────────────┐
│ load → normalize to Artifact → redact PII → chunk → embed              │
│ → LLM entity/relation extraction → upsert                              │
└───────────────┬──────────────────────────────────┬─────────────────────┘
                ▼                                  ▼
     ┌─────────────────────┐            ┌─────────────────────────┐
     │  Azure AI Search    │            │ Project Knowledge Graph │
     │  hybrid vector+kw   │            │ NetworkX → graph.json   │
     └─────────┬───────────┘            └────────────┬────────────┘
               └──────────────┬──────────────────────┘
                              ▼
┌──────────────────────────── AGENTS ────────────────────────────────────┐
│ Gap & risk analyzer │ Interview agent │ Artifact generator │ Copilot   │
└───────────────┬────────────────────────────────────────────┬───────────┘
                ▼                                            ▼
     ┌──────────────────────────┐          ┌──────────────────────────────┐
     │  FastAPI backend         │          │ Teams clients (thin)         │
     │  (single API for all)    │◄─────────│ • Bot: @mention → /ask       │
     │  + POST /api/messages    │          │ • Bot 1:1: interview cards   │
     │  + POST /webhooks/graph  │          │ • Static tab: risk dashboard │
     │  + APScheduler jobs      │          └──────────────────────────────┘
     │  + consent store (SQLite)│                   ▲
     └──────────────────────────┘          Azure Bot Service + Entra ID app
```

**Key design principle:** the backend is **API-first**. The Teams tab and the bot are *clients*: bot handlers parse an activity, call an existing endpoint, and render the result as text or an Adaptive Card. **No business logic in bot handlers.**

**Second principle:** every external service (LLM, search, **Microsoft Graph, Teams activities**) has a **mock implementation** behind the same interface, switched with `USE_MOCKS=true`. The whole app — bot logic included — runs and tests offline with no Azure and no M365 tenant.

---

## 7. Tech stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2, uvicorn | API-first |
| Config | pydantic-settings, `.env` | Commit only `.env.example` |
| LLM | Azure OpenAI via Azure AI Foundry (`openai` SDK, `AzureOpenAI` client) | One chat deployment + one embedding deployment |
| Retrieval | Azure AI Search (`azure-search-documents`) | Hybrid vector + keyword, filter by artifact type |
| Knowledge graph | NetworkX, persisted to `data/graph.json` | Behind `GraphStore` interface; Cosmos DB later if needed |
| Frontend | React + Vite + TypeScript | Risk dashboard **as a Teams static tab** (`@microsoft/teams-js` for context/theme); plain browser mode kept for local dev |
| Teams bot | **Microsoft 365 Agents SDK (Python)** — `microsoft-agents-hosting-core`, `microsoft-agents-hosting-fastapi`, `microsoft-agents-hosting-msteams` | Hosted **inside the existing FastAPI app**, not a separate service. Python 3.10+; verified on PyPI 17 Oct 2026. ⚠️ Microsoft documents the Teams hosting layer as *still in flux* — pin versions and wrap it thinly |
| Bot channel | Azure Bot Service (free tier) + Entra ID app registration | Routes Teams activities to `POST /api/messages` |
| Teams app package | `manifest.json` with RSC permissions + icons, sideloaded | `ChannelMessage.Read.Group`, `ChatMessage.Read.Chat` under `authorization.permissions.resourceSpecific` |
| Graph access | `azure-identity` + `msgraph-sdk` (fallback: thin `httpx` client) | Client-credentials flow. Final choice in the Graph-connector task — whichever is lighter wins |
| Scheduling | APScheduler inside FastAPI | Backfill, catch-up, webhook renewal. Single process; a real queue is post-hackathon |
| Consent / sync state | SQLite (`data/legacylens.db`) | Consent records, connected conversations, webhook subscriptions, delta tokens. Cosmos DB / Table Storage noted in §18 |
| Public endpoint | Dev tunnels (or ngrok) for dev; Azure App Service / Container Apps for the demo | Bot and Graph webhooks both need public HTTPS |
| Voice (stretch) | Azure AI Speech | STT/TTS for interviews |
| ~~Copilot Studio~~ | — | **Superseded by the Agents SDK bot.** Kept as a fallback only if the SDK path stalls |
| Tests | pytest + FastAPI TestClient | Smoke tests in mock mode |
| Source control | GitHub, one branch per module, PRs into `main` | |

---

## 8. Repository structure

```
legacylens/
├── CLAUDE.md                 # Short instructions for Claude Code; references this doc
├── Plan.md                   # This file (repo root, not docs/)
├── TASKS.md                  # Task board
├── AzureResources.md         # Provisioned Azure inventory
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app, CORS, router registration
│   │   ├── config.py         # Settings (env vars, USE_MOCKS)
│   │   ├── models.py         # SHARED CONTRACTS — source of truth
│   │   ├── api/              # Routers: ingest, risks, interviews, artifacts, ask, graph,
│   │   │                     #   people, bot (POST /api/messages), webhooks (Graph)
│   │   ├── bot/              # NEW — Teams client layer, no business logic
│   │   │   ├── agent.py      # Agents SDK app: activity handlers (message, installed, invoke)
│   │   │   ├── cards.py      # Adaptive Cards: consent, interview question, approve, answer
│   │   │   └── mock_turn.py  # Fake turn context so handlers are testable without Teams
│   │   ├── jobs/             # NEW — APScheduler: backfill, catch-up, subscription renewal
│   │   ├── services/
│   │   │   ├── llm.py        # Chat + embeddings (real + mock)
│   │   │   ├── search.py     # AI Search wrapper (real + in-memory mock)
│   │   │   ├── graph_api.py  # NEW — Microsoft Graph client (real + mock, same interface)
│   │   │   └── consent_store.py # NEW — SQLite: consent, connected chats, subs, delta tokens
│   │   ├── ingestion/        # loaders, normalizer, redactor, chunker, extractor, pipeline
│   │   │   └── graph_loaders.py # NEW — Teams/Outlook/SharePoint payloads → Artifact
│   │   ├── graph/            # GraphStore interface + NetworkX implementation
│   │   └── agents/
│   │       ├── prompts.py    # ALL LLM prompts live here
│   │       ├── gap_analyzer.py
│   │       ├── interviewer.py
│   │       ├── generator.py
│   │       └── copilot.py
│   ├── tests/
│   └── requirements.txt
├── scripts/
│   ├── generate_dataset.py   # Synthetic project data with planted risks
│   └── ingest.py             # One-command ingestion
├── data/
│   ├── sample/               # Generated artifacts (JSON) — the project's "past history"
│   ├── graph.json            # Persisted knowledge graph
│   └── legacylens.db         # Consent + sync state (gitignored)
├── frontend/                 # React + Vite dashboard, served as a Teams static tab
├── bot/                      # Teams app package + setup notes
│   ├── manifest/             # manifest.json (RSC permissions, tab, bot), color.png, outline.png
│   └── README.md             # Entra app, Azure Bot, dev tunnel, sideload steps
├── .env.example
└── README.md
```

---

## 9. Data model (shared contracts — `backend/app/models.py`)

> **Rule:** `models.py` is the team's API contract. Do not change it without telling the team.

| Model | Key fields |
|---|---|
| `Person` | id, name, email, role, team, is_departing (bool), departure_date (optional) |
| `Artifact` | id, type (`email` \| `chat` \| `ticket` \| `doc` \| `commit` \| `interview`), source_id, title, body, author_id, participant_ids, timestamp, url_or_path, metadata |
| `Chunk` | id, artifact_id, text, embedding (optional), topic_ids |
| `Entity` | id, type (`system` \| `incident` \| `decision` \| `process` \| `person`), name, aliases, description |
| `Edge` | source_id, target_id, relation, weight, evidence_artifact_ids |
| `Citation` | artifact_id, artifact_type, title, snippet, url_or_path |
| `RiskItem` | id, topic_entity_id, topic_name, owner_candidates (person ids), bus_factor, coverage_score, activity_score, risk_score, risk_level (`high` \| `medium` \| `low`), evidence: list[Citation] |
| `InterviewSession` | id, risk_id, interviewee_id, status (`open` \| `completed`), questions, answers, created_at |
| `InterviewQuestion` | id, text, rationale, evidence: list[Citation] |
| `InterviewAnswer` | question_id, text, answered_at, approved (bool) |
| `GeneratedDoc` | id, type (`runbook` \| `handover` \| `decision_log`), title, subject_id (topic or person), markdown, citations, generated_at |
| `AskRequest` / `AskResponse` | question, optional filters / answer, citations, confidence |
| `ConsentRecord` *(planned)* | id, scope_type (`channel` \| `chat` \| `mailbox` \| `site`), scope_id (Teams conversation / channel / mailbox / site id), display_name, status (`granted` \| `paused` \| `declined`), granted_by_person_id, granted_at, paused_at, tenant_id |

**`ConsentRecord` is a new concept, not yet a model.** Live ingestion needs it: nothing is ingested from a conversation, mailbox or site without a `granted` record, and `@LegacyLens pause` flips it to `paused`. It is also the audit trail we show judges (§15). Adding it touches `models.py`, which is the team contract — so it goes in **through the team process** (agree the shape, then add as a new model with optional fields only), **not** as part of this plan edit.

Three more pieces of state live **only** in the SQLite store and deliberately *not* in `models.py`, because they are plumbing rather than contracts:

| Store table | Holds | Why |
|---|---|---|
| `connected_sources` | conversation/channel/mailbox/site id, service url, last backfill time | Where to listen and where to reply |
| `webhook_subscriptions` | Graph subscription id, resource, expiry, client state | Chat-message subscriptions expire in **60 minutes** and must be renewed |
| `delta_tokens` | resource → delta/skip token | Incremental SharePoint and mail sync without re-reading everything |

---

## 10. Knowledge graph schema

### Node types
| Node | Examples | Key properties |
|---|---|---|
| `Person` | Ravi, Priya, Arjun, Meera | role, is_departing |
| `System` | Payment gateway, Settlement batch job, Kafka cluster | description, criticality |
| `Incident` | INC-142 "Gateway timeouts" | severity, opened_at, resolved_at |
| `Decision` | "Chose Kafka over RabbitMQ" | date, rationale_summary |
| `Process` | Release process, On-call rotation | |
| `Artifact` | an email, ticket, doc, commit, interview | type, timestamp |

### Edge types
| Edge | From → To | Meaning |
|---|---|---|
| `RESOLVED` | Person → Incident | Person fixed the incident (strong expertise signal) |
| `AFFECTS` | Incident → System | Incident is about a system |
| `COMMITTED_TO` | Person → System | Code commits touching a system |
| `AUTHORED` | Person → Artifact | Wrote the artifact |
| `MENTIONS` | Artifact → Entity | Artifact discusses an entity |
| `DECIDED` | Person → Decision | Person made / drove a decision |
| `ABOUT` | Decision → System | Decision concerns a system |
| `DOCUMENTED_BY` | System/Process/Decision → Artifact(doc/interview) | Formal documentation exists |
| `OWNS` | Person → System | Inferred or declared ownership |

Edges carry `weight` and `evidence_artifact_ids` so every risk and answer can be cited.

---

## 11. Core algorithms

### 11.1 Ingestion pipeline
1. **Load** raw sources: synthetic JSON (`ingestion/loaders.py`) *and* live Microsoft 365 payloads (`ingestion/graph_loaders.py`) — see §11.6. Both produce `Artifact`s, so the rest of the pipeline is identical.
2. **Normalize** into `Artifact`.
3. **Redact** basic PII (phone numbers, personal emails outside the org, card numbers).
4. **Chunk** (~500–800 tokens, overlap ~100) and **embed**.
5. **Extract** entities + relations with the LLM (structured JSON output), resolve aliases ("PG", "payment gateway", "gateway" → one `System`).
6. **Upsert** chunks into Azure AI Search and nodes/edges into the graph.

Interview answers are ingested through the same pipeline as `Artifact(type="interview")` and create `DOCUMENTED_BY` edges.

### 11.2 Gap & risk analyzer
For each topic entity (System / Process / Decision):

- **Activity score:** weighted count of interactions.
  Weights: `RESOLVED` 5, `COMMITTED_TO` 3, `DECIDED` 4, authored doc 2, chat/email mention 1. Recency-boost recent activity.
- **Bus factor:** number of distinct people whose share of the topic's weighted activity is ≥ 15%. A single person holding ≥ 70% is flagged as a **single-person risk**.
- **Coverage score (0–1):** strength of search hits for the topic restricted to `doc` and `interview` artifacts, plus presence of `DOCUMENTED_BY` edges.
- **Risk score:**
  `risk = normalized_activity × (1 / bus_factor) × (1 − coverage)`
  Boost ×1.5 if the top owner `is_departing`.
- **Risk level:** high ≥ 0.6, medium ≥ 0.3, else low.
- Output ranked `RiskItem`s with evidence citations.

**Acceptance check:** on the sample dataset, the three planted risks rank in the top 3.

### 11.3 Interview agent
1. Take a `RiskItem` and pick the interviewee (top owner candidate, prefer departing people).
2. Retrieve evidence (incidents, chats, commits) for the topic.
3. Generate 3–5 **grounded** questions. Each question must reference concrete evidence and target tacit knowledge: triggers, first checks, decision criteria, workarounds, gotchas, who to escalate to.
4. Ask questions one at a time (Teams / web). Allow a short follow-up when an answer is vague.
5. Store answers. The interviewee can **review and approve** before they are published.
6. Ingest approved answers back into the KB and recompute the risk.

### 11.4 Artifact generator
- **Runbook (per topic):** Overview, symptoms/alerts, diagnosis steps, fix steps, verification, escalation, known gotchas. Every step cites an interview answer or source artifact.
- **Handover pack (per person):** Systems owned, open risks, key decisions and their rationale, recurring tasks, contacts, links to runbooks, unresolved questions.
- **Decision log (stretch):** Decision, date, context, options considered, rationale, people involved, sources.
- Output is Markdown in `GeneratedDoc`.

### 11.5 Project Copilot (Q&A)
- Hybrid retrieval from AI Search plus graph expansion (e.g. related incidents for a system, the person who resolved them).
- Answer only from retrieved context. Cite every claim. If the context is insufficient, say so and suggest creating an interview.
- Supports person-centric queries: "How did Ravi usually handle gateway timeouts?"

### 11.6 Live ingestion & consent (new, 17 Oct)

**Consent gate — nothing is ingested without it.**

1. The app is installed into a project channel or group chat. The bot receives a `conversationUpdate` / install activity and posts an Adaptive Card: *"Allow LegacyLens for **Payments Platform** to learn from this conversation?"* **[Allow] [Not now]**.
2. **Allow** → write a `ConsentRecord(status="granted")` with who approved and when, then (a) backfill recent history and (b) start accepting live messages.
3. **Not now** → write `declined` and ignore the conversation. No message from it ever reaches the pipeline.
4. `@LegacyLens pause` → flip to `paused`, drop any Graph subscription for that scope, stop ingesting. The bot confirms in the chat.

Every adapter calls the same gate (`consent_store.is_granted(scope_id)`) before it emits an `Artifact`. That single choke point is what makes the privacy claim in §15 true rather than aspirational.

**Real-time path**

- **Teams (primary):** the app manifest declares RSC `ChannelMessage.Read.Group` (channels) and `ChatMessage.Read.Chat` (group chats) under `authorization.permissions.resourceSpecific`, so the bot receives every message in the conversations where it is installed — no @mention needed. The handler maps the activity to `Artifact(type="chat")` and runs the pipeline for that one artifact.
- **Backup:** Graph change-notification subscriptions on `/chats/{id}/messages`. These expire after **60 minutes**; a request for more than 1 hour out requires a `lifecycleNotificationUrl`, and anything under 45 minutes is snapped to 45. A renewal job therefore runs well inside the hour (§11.6 periodic path).
- **Prefer a channel over a group chat for the demo.** All-messages delivery in group chats is reported as unreliable (§17).
- Teams Graph APIs are **no longer metered** (since 25 Aug 2025) and the `model` query parameter is ignored, so there is no billing configuration to do.

**Periodic path (APScheduler, inside FastAPI)**

| Job | Cadence | Purpose |
|---|---|---|
| `backfill(scope)` | once, on consent | Pull recent channel/chat history so a topic has a past, not just a present |
| `catch_up` | ~15 min | Re-read since the last delta token in case a webhook or activity was dropped |
| `renew_subscriptions` | ~30 min | Renew Graph subscriptions before the 60-minute expiry |
| `recompute_risks` | after each ingest batch | Keep `GET /risks` and the Teams tab current |

**Mail and files**

- **Outlook:** one **shared project mailbox** (e.g. `payments-platform@<tenant>`) that the team CCs. Because the mailbox is a deliberate CC target, consent is implicit in the act of CC'ing — but the mailbox still gets a `ConsentRecord` for the audit trail. A second **LLM relevance filter** ("is this message about this project?") drops noise before chunking; its prompt lives in `agents/prompts.py` like every other prompt.
- **SharePoint:** only the project site's document library, via `Sites.Selected` plus a per-site grant, read with `/sites/{id}/drive/root/delta` so each sync is incremental.
- **Deduplication:** every artifact keeps its Graph message/item id as `source_id`, and loaders de-dupe on it, so a webhook and a catch-up reading the same message produce one artifact.

**Mock mode.** `services/graph_api.py` has a mock that replays canned Teams/mail/SharePoint payloads, and `bot/mock_turn.py` fakes a turn context, so consent, @mention answering and interview cards are all testable with `USE_MOCKS=true` — no tenant, no tunnel, no Azure.

---

## 12. API specification

| Method | Path | Purpose | Returns |
|---|---|---|---|
| GET | `/health` | Health check | status, mock mode |
| POST | `/ingest` | Ingest `data/sample` (or a given path) | counts of artifacts, chunks, entities, edges |
| GET | `/risks` | Ranked knowledge risks | `list[RiskItem]` |
| GET | `/risks/{risk_id}` | Risk detail with evidence | `RiskItem` |
| GET | `/people` | People with knowledge concentration summary | `list[Person]` + stats |
| GET | `/graph` | Nodes and edges (for visualization) | graph JSON |
| POST | `/interviews/{risk_id}/start` | Create interview with grounded questions | `InterviewSession` |
| GET | `/interviews/{session_id}` | Session state | `InterviewSession` |
| POST | `/interviews/{session_id}/answer` | Submit answer to a question | updated `InterviewSession` |
| POST | `/interviews/{session_id}/approve` | Approve and publish answers into KB | updated risk |
| POST | `/artifacts/runbook` | Generate runbook for a topic | `GeneratedDoc` |
| POST | `/artifacts/handover` | Generate handover pack for a person | `GeneratedDoc` |
| POST | `/artifacts/decision-log` | (stretch) Decision log for a topic | `GeneratedDoc` |
| POST | `/ask` | Project Copilot Q&A | `AskResponse` |
| POST | `/api/messages` | **New — Teams bot endpoint.** Receives every Teams activity from Azure Bot Service (messages, installs, card submits). Delegates to `/ask`, `/interviews/*` and the consent store; holds no business logic | `200` + bot replies sent over the Bot Service |
| POST | `/webhooks/graph` | **New — Graph change notifications.** Echoes `validationToken` on subscription creation, verifies `clientState`, then queues each notification for ingestion | `200` (plain-text token on validation) |
| POST | `/webhooks/graph/lifecycle` | **New —** lifecycle notifications (reauthorization / subscription removed), required for subscriptions longer than 1 hour | `200` |
| GET | `/sources` | **New (optional) —** consented scopes and their sync state, for the tab's "connected sources" panel | `list[ConsentRecord]` + last-sync times |

All endpoints must work with `USE_MOCKS=true`. The two webhook endpoints must also be safe to call unauthenticated-but-verified: validate `clientState`, never trust the payload body as content (Graph notifications carry ids; we fetch the real message ourselves).

---

## 13. Synthetic dataset spec (`scripts/generate_dataset.py`)

**Project:** "Payments Platform" (fictional).

| Person | Role | Notes |
|---|---|---|
| Ravi | Senior backend engineer | **Departing** (on notice) |
| Priya | Solution architect | Owns architecture decisions |
| Arjun | Support lead | Runs settlement operations |
| Meera | New joiner | Successor; uses the Copilot |

**Volume:** ~60 emails/chats, ~20 tickets, ~6 wiki docs, ~40 commits, written to `data/sample/*.json`.

**Planted risks (must be detected):**
1. **Payment gateway outages:** Ravi resolved 4 incidents (INC-142, INC-155, INC-171, INC-188); no wiki page exists; the fix lives in chat threads.
2. **Settlement batch job:** only Arjun touches it; tribal workarounds ("rerun with `--skip-recon` if the bank file is late") exist only in chats.
3. **Message queue decision:** why Kafka was chosen over RabbitMQ is discussed only in Priya's email threads; no decision record.

**Well-documented contrast topics:** release process, onboarding, API authentication (multiple people, wiki pages exist).

**Requirements:** deterministic (fixed seed, templated, no LLM required); optional `--use-llm` flag to enrich text.

**Role after the 17 Oct pivot:** the synthetic dataset is **not** replaced by live data — it *is* the project's past. A fresh tenant has no six-month history, so risk detection would have nothing to chew on. The dataset stays the "past 6 months", live Microsoft 365 messages are the present, and both are cited side by side in the demo. The planted risks must stay detectable: the acceptance test (three planted risks in the top 3) continues to gate every change here.

---

## 14. Configuration (`.env.example`)

```
USE_MOCKS=true

AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=
AZURE_OPENAI_CHAT_DEPLOYMENT=
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=

AZURE_SEARCH_ENDPOINT=
AZURE_SEARCH_API_KEY=
AZURE_SEARCH_INDEX_NAME=legacylens-chunks

DATA_DIR=./data
GRAPH_PATH=./data/graph.json

# --- Teams bot (Azure Bot Service + Entra ID app) -------------------------
BOT_APP_ID=
BOT_APP_PASSWORD=
BOT_APP_TENANT_ID=
BOT_APP_TYPE=SingleTenant

# --- Microsoft Graph (client credentials) ---------------------------------
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=

# --- Live sources ---------------------------------------------------------
PROJECT_MAILBOX=                       # e.g. payments-platform@<tenant>.onmicrosoft.com
SHAREPOINT_SITE_ID=                    # project site only (Sites.Selected grant)
TEAMS_DEMO_CHANNEL_ID=                 # primary demo surface

# --- Public endpoint + state ---------------------------------------------
PUBLIC_BASE_URL=                       # dev tunnel / App Service URL; bot + webhook callbacks
GRAPH_WEBHOOK_CLIENT_STATE=            # random secret echoed back by Graph; verify on every call
CONSENT_DB_PATH=./data/legacylens.db
ENABLE_SCHEDULER=true
```

`.env.example` carries these keys with empty values; real values go only in `.env`, which is gitignored. **Never commit secrets.** With `USE_MOCKS=true` every key above can stay blank.

---

## 15. Privacy, consent and responsible AI

These are part of the pitch. Judges will ask about them.

- **Per-conversation consent, in the open:** when the app is installed, the bot asks *in the chat itself* whether it may learn from that conversation. Nothing is ingested until someone clicks **Allow**, and the `ConsentRecord` stores who approved and when. Consent is visible to everyone in the room — not buried in an admin console.
- **Reversible:** `@LegacyLens pause` revokes consent for that conversation and stops ingestion immediately.
- **Least privilege, enforced by scope, not promises:**
  - Teams: RSC only — the bot sees the conversations it was installed into, nothing else.
  - Outlook: **one shared project mailbox.** ⚠️ Granting the `Mail.Read` *application* permission in Entra would hand the app tenant-wide mailbox access that Exchange RBAC cannot claw back. So we do **not** consent `Mail.Read` tenant-wide; the role is granted through an **Exchange Online RBAC for Applications** assignment with a resource scope limited to that one mailbox.
  - SharePoint: `Sites.Selected` plus a grant on the single project site. Consented permission alone gives access to nothing.
- **No tenant-wide watching.** We deliberately do *not* subscribe to `/chats/getAllMessages` or auto-install the app into new chats in the MVP. That capability exists (§18) but it conflicts with everything on this list, so it stays off and admin-gated.
- **PII redaction** in the ingestion pipeline.
- **Human review:** interviewees approve captured knowledge before it is published.
- **Transparency:** every generated answer and document carries citations; no uncited claims.
- **No performance surveillance:** risk scores describe *knowledge concentration*, not individual productivity. The dashboard shows topics, not employee rankings.
- **Retention:** raw source data can be purged after knowledge is captured; generated docs remain.
- **Data stays in our own Azure resources** (AI Search + Azure OpenAI in the project subscription); nothing is sent to third-party services.


---

## 16. Team conventions

- **Contracts first:** don't change `models.py` or endpoint shapes without discussing with the team.
- **One branch per module**, small PRs into `main`, `main` must always run in mock mode.
- **Prompts live in `agents/prompts.py`** only.
- **Mock parity:** every real service has a mock with the same interface.
- **Tests:** add or update a smoke test for any endpoint you touch; `pytest` must pass before merging.
- **Secrets:** never commit `.env` or keys.
- **Demo data is sacred:** changes to the dataset generator must keep the planted risks detectable.

---

## 17. Risks and mitigations

| Risk | Mitigation |
|---|---|
| **#1 — No M365 tenant.** Everything Teams-related needs a tenant with **admin consent** and **custom app sideloading** enabled. Free M365 Developer Program sandboxes are now restricted to Visual Studio Professional/Enterprise *standard* subscribers (monthly subs don't count) and partner-program members; personal Microsoft accounts can no longer join at all. | Chase in parallel, in this order: hackathon-provided tenant → a teammate's VS subscription → an M365 Business trial. **Decision point: if no tenant with admin consent within ~1 day, fall back to the existing mock + web-UI plan** (the backend is done and demoable as-is). Keep that fallback runnable, don't delete the web dashboard. |
| **Hackathon end date unknown** — can't schedule the cut lines. | Fill in the date at the top of this doc as soon as it's known; until then treat the stretch list as genuinely optional. |
| **RSC all-messages delivery in group chats is unreliable** (reported as flaky/preview). | Use a **project channel** as the primary demo surface; keep Graph `/chats/{id}/messages` subscriptions as the backup path for group chats. |
| **Graph subscriptions expire in 60 minutes** — a demo that runs long goes silent. | `renew_subscriptions` job every ~30 min; `lifecycleNotificationUrl` wired up; the bot-activity path (not webhooks) is the primary capture route, so expiry can't kill the demo. |
| **Real-Azure smoke test has never run with real keys** (T02 passes only on the missing-keys path). | Run it with `USE_MOCKS=false` **early**, before the bot work — it is the cheapest way to find a bad key or a region/quota surprise. |
| Public HTTPS endpoint needed for both bot and webhooks; dev tunnel URLs churn. | `PUBLIC_BASE_URL` in config, re-register the bot messaging endpoint when it changes; deploy to App Service before the rehearsal. |
| Agents SDK Teams hosting layer is **documented as still in flux**. | Pin exact versions; keep bot handlers thin so a breaking change touches one file; all logic stays behind existing endpoints. |
| A fresh tenant has no history, so risks can't be detected from live data alone. | Hybrid demo: synthetic dataset is the "past 6 months", live messages are the present (§13, §21). |
| Azure quota / provisioning delays | Mock mode for everything; core Azure resources already provisioned (`AzureResources.md`) |
| LLM output not valid JSON | Structured outputs / JSON mode, Pydantic validation, retry once, fall back to mock |
| Planted risks don't surface | Deterministic dataset + acceptance test on risk ranking |
| Live demo failure | Recorded backup video; cached responses for the demo flow |
| Scope creep | Teams MVP is G6–G10 only; email and SharePoint connectors are the first things cut |

---

## 18. Future roadmap (post-hackathon)

- **Per-user mailbox ingestion** with delegated consent and folder / category / subject-tag filters, so individuals can contribute project mail without exposing a whole mailbox.
- **Auto-detecting new project chats:** subscribe to `/chats` and install the app via Graph (`TeamsAppInstallation.ReadWriteAndConsentForChat`-class permissions) so new group chats are picked up without a manual install. **Admin-enabled only** — it trades away the per-conversation consent moment that §15 is built on, so it must stay opt-in at tenant level and announced to users.
- **Cosmos DB or Azure Table Storage** for consent records, sync state and the knowledge graph, replacing SQLite + `graph.json` once more than one process runs.
- A hosted multi-project deployment with Entra ID auth on the tab.
- Azure DevOps / Jira / GitHub connectors.
- Proactive triggers: when HR marks someone as departing, interviews auto-schedule.
- Continuous project health score and alerts when bus factor drops to 1.
- Client handover packs as a Presidio services offering.
- Multi-project, multi-tenant deployment with Entra ID auth.

---

## 19. Glossary

- **Tacit knowledge:** know-how that exists in people's heads and was never documented.
- **Bus factor:** number of people who would have to leave before a topic becomes unknown to the team.
- **Coverage:** how well a topic is formally documented (docs, runbooks, approved interviews).
- **Grounded question:** an interview question tied to specific evidence (tickets, chats, commits).
- **Walking skeleton:** a minimal end-to-end implementation of every module, used to unblock parallel work.

---

## 20. Instructions for Claude Code

- Read this document before making changes. It describes intent; the code is the implementation.
- Respect the contracts in `backend/app/models.py`. If a change is required, explain why and update this doc.
- Keep `USE_MOCKS=true` working at all times. Every new external call needs a mock.
- Put every LLM prompt in `backend/app/agents/prompts.py`.
- After changes: run `pytest` and start the app to verify. Fix what breaks.
- Keep code simple and readable. This is a hackathon project: no auth, no premature abstractions beyond `GraphStore` and the mock/real service split.
- Every generated answer or document must include citations.
- **Bot handlers are clients, not logic.** A handler parses an activity, calls an existing endpoint, renders text or an Adaptive Card. If you find yourself scoring a risk or building a prompt in `app/bot/`, it belongs in `agents/`.
- **Never ingest without a consent check.** Every live adapter goes through the consent gate in §11.6 before producing an `Artifact`.
- Microsoft Graph and Teams activities need mocks like everything else: `USE_MOCKS=true` must exercise consent, @mention answering and interview cards with no tenant and no tunnel.
- Research before touching Graph, the Agents SDK or the manifest — these APIs move fast and this doc's citations have dates on them.
- Commit after each completed step with a clear message.

---

## 21. Decision log: pivot to Teams + real data (17 Oct 2026)

**What changed.** LegacyLens stops being a standalone React web app over synthetic data and becomes a **Microsoft Teams app over real Microsoft 365 data**. The backend does not change direction: T00–T25 (ingestion, graph, risk analyzer, interviews, generators, Copilot — 212 tests green in mock mode) stay exactly as they are. What changes is **who calls them** and **where artifacts come from**.

**Why.**
1. *Knowledge lives where work happens.* Asking a team to visit a dashboard to capture tacit knowledge repeats the failure mode we are pitching against — handover docs nobody opens. A bot in the channel they already use meets them where they are.
2. *A real connector is the hard part, and judges know it.* Ingesting live Teams/Outlook/SharePoint traffic with per-conversation consent is more convincing than any amount of synthetic polish.
3. *Teams Graph APIs are no longer metered* (since 25 Aug 2025), so the cost objection to live ingestion is gone.
4. *The backend was already API-first*, so the pivot is additive: new clients and new loaders, no rewrite.

**The seven decisions.**

| # | Decision | Notes |
|---|---|---|
| 1 | **Teams-first surface.** Bot (M365 Agents SDK, Python) hosted inside the existing FastAPI app + Azure Bot Service. @mention in a channel → `/ask`; 1:1 Adaptive Cards → `/interviews/*`. Dashboard becomes a Teams **static tab**. | No business logic in bot handlers (§6) |
| 2 | **Real sources via Graph, narrowly scoped.** Teams via RSC; one shared project mailbox via Exchange RBAC with a resource scope; one SharePoint library via `Sites.Selected`. | §11.6, §15 |
| 3 | **Real-time + periodic.** Bot activities and webhooks for now; APScheduler for backfill, catch-up and subscription renewal. | §11.6 |
| 4 | **Consent card per conversation**, stored as a `ConsentRecord`, revocable with `@LegacyLens pause`. | §15 |
| 5 | **Hybrid demo.** Synthetic dataset = past 6 months; a message posted live during the demo = the present; both cited in one answer. | §13 |
| 6 | **`USE_MOCKS=true` still runs everything** — mock Graph client, fake turn context, bot logic testable without Teams. Non-negotiable, unchanged. | §6 |
| 7 | **New infrastructure:** Entra ID app registration, Azure Bot Service (free), public HTTPS (dev tunnel → App Service), SQLite for consent and sync state. Knowledge graph stays NetworkX + `graph.json`. | §7, §14 |

**What this costs us.** The Teams tab is a slower path to a visible dashboard than a plain web app, and the email/SharePoint connectors are genuinely optional — they are the first things cut. The single biggest exposure is tenant access (§17): without an M365 tenant with admin consent and sideloading, none of decisions 1–4 can be demonstrated, and the fallback is the already-working mock + web plan.

**Superseded.** The Copilot Studio Teams path (old T31) is replaced by the Agents SDK bot, kept only as a fallback. The standalone web interview page (old T28) is replaced by interviews in 1:1 Teams chat.

**Verified on 17 Oct 2026** (sources behind the claims above):
- [M365 Agents SDK for Python](https://github.com/microsoft/Agents-for-python) — `microsoft-agents-hosting-fastapi` / `-msteams` / `-core`, Python 3.10+, Teams layer "still in flux".
- [Get all channel and chat messages (RSC)](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-messages-for-bots-and-agents) — `ChannelMessage.Read.Group`, `ChatMessage.Read.Chat`.
- [Teams licensing / metering](https://learn.microsoft.com/en-gb/graph/teams-licenses) and [metered API list](https://learn.microsoft.com/en-us/graph/metered-api-list) — un-metered since 25 Aug 2025.
- [Change notifications for Teams resources](https://learn.microsoft.com/en-us/graph/teams-changenotifications-chatmessage) — 60-minute max, lifecycle URL beyond 1 hour.
- [Exchange Online RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac) — resource-scoped app roles; a tenant-wide Entra `Mail.Read` consent cannot be scoped down.
- [Sites.Selected overview](https://learn.microsoft.com/en-us/graph/permissions-selected-overview) and [drive delta](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/scan-guidance).
- [M365 Developer Program eligibility](https://learn.microsoft.com/en-us/office/developer-program/join-with-visual-studio) — VS Pro/Enterprise standard or partner programs; no personal accounts.
