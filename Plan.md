# LegacyLens — Project Plan

> **Track:** Knowledge Management & Preservation (Microsoft Hackathon)
> **Status:** Day 1 — base code setup (hackathon started 14 Oct 2026)
> **Purpose of this doc:** Single source of truth for what LegacyLens is, how it is built, and how the team works on it. Claude Code and every teammate should read this before changing code.

---

## 1. One-line summary

LegacyLens is an AI agent that continuously captures an entire project's knowledge by analyzing its emails, Teams chats, tickets, documents, and code, identifying undocumented areas and single-person knowledge risks, and running targeted AI-led interviews with the right team members. It builds a living project knowledge graph, auto-generates runbooks, decision logs, and handover packs, and offers a Teams Project Copilot, so no knowledge is lost when people leave, rotate, or hand over the project.

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

1. **Observe:** Ingest the project's emails, Teams chats, tickets, wiki/SharePoint docs, and code commits (with consent).
2. **Detect risk:** Build a knowledge graph of people, systems, incidents, and decisions. Score each topic for *bus factor* (how many people know it) and *documentation coverage*. Surface single-person knowledge risks and undocumented areas.
3. **Interview:** For each high-risk topic, an AI agent interviews the right person in Teams with targeted, evidence-grounded questions (e.g. "In INC-142 and INC-188 you restarted the settlement worker. What told you that was the fix?").
4. **Publish:** Turn answers into runbooks, decision logs, and handover packs, all with citations. Answers are ingested back into the knowledge base, so risk scores drop and the Project Copilot gets smarter.

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

### Goals (MVP)
- G1: Ingest a synthetic project dataset (emails, chats, tickets, docs, commits) into search + knowledge graph.
- G2: Compute and rank knowledge risks per topic (bus factor × coverage × activity).
- G3: Run an AI interview for a risk with grounded questions; store answers back into the KB.
- G4: Generate at least one **runbook** and one **handover pack** with citations.
- G5: Project Copilot Q&A with citations (web UI; Teams via Copilot Studio if time allows).
- G6: Risk dashboard that visibly updates after an interview (red → green).

### Stretch
- Decision log generation.
- Voice interviews via Azure AI Speech.
- Live Microsoft Graph ingestion for a real (test) tenant.
- Knowledge graph visualization.

### Non-goals (for the hackathon)
- Production auth, multi-tenancy, RBAC.
- Real-time continuous sync (batch ingestion is fine).
- Full PII/DLP pipeline (we describe it and implement a basic redaction step).

---

## 6. Architecture

```
┌────────────────────────────── SOURCES ──────────────────────────────┐
│  Email + Teams chats   │  SharePoint/Wiki docs  │  Tickets  │ Commits │
│  (Microsoft Graph API — mocked with synthetic data for the demo)    │
└───────────────────────────────┬─────────────────────────────────────┘
                                ▼
┌─────────────────────── INGESTION PIPELINE ──────────────────────────┐
│ load → normalize to Artifact → redact PII → chunk → embed           │
│ → LLM entity/relation extraction (people, systems, incidents,       │
│   decisions, processes)                                             │
└───────────────┬──────────────────────────────────┬──────────────────┘
                ▼                                  ▼
     ┌─────────────────────┐            ┌─────────────────────────┐
     │  Azure AI Search    │            │ Project Knowledge Graph │
     │  hybrid vector+kw   │            │ NetworkX → JSON (MVP)   │
     └─────────┬───────────┘            └────────────┬────────────┘
               └──────────────┬──────────────────────┘
                              ▼
┌──────────────────────────── AGENTS ─────────────────────────────────┐
│ Gap & risk analyzer │ Interview agent │ Artifact generator │ Copilot│
└───────────────┬─────────────────────────────────────────┬───────────┘
                ▼                                         ▼
     ┌──────────────────────┐               ┌──────────────────────────┐
     │ FastAPI REST backend │◄──────────────│ Clients:                 │
     │ (single API for all) │               │ • React risk dashboard   │
     └──────────────────────┘               │ • Teams (Copilot Studio /│
                                            │   M365 Agents SDK)       │
                                            └──────────────────────────┘
```

**Key design principle:** the backend is **API-first**. The dashboard, the Teams bot, and Copilot Studio all call the same FastAPI endpoints. No business logic lives in clients.

**Second principle:** every external service (LLM, search) has a **mock implementation** behind the same interface, switched with `USE_MOCKS=true`. Anyone can run the whole app without Azure keys.

---

## 7. Tech stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2, uvicorn | API-first |
| Config | pydantic-settings, `.env` | Commit only `.env.example` |
| LLM | Azure OpenAI via Azure AI Foundry (`openai` SDK, `AzureOpenAI` client) | One chat deployment + one embedding deployment |
| Retrieval | Azure AI Search (`azure-search-documents`) | Hybrid vector + keyword, filter by artifact type |
| Knowledge graph | NetworkX, persisted to `data/graph.json` | Behind `GraphStore` interface; Cosmos DB later if needed |
| Frontend | React + Vite + TypeScript | Risk dashboard, interview view, ask page |
| Teams | Copilot Studio (low-code) calling FastAPI, or Microsoft 365 Agents SDK (code-first) | Decide on Day 2 |
| Voice (stretch) | Azure AI Speech | STT/TTS for interviews |
| Source integration (stretch) | Microsoft Graph API | Mail, Teams messages, SharePoint files |
| Tests | pytest + FastAPI TestClient | Smoke tests in mock mode |
| Source control | GitHub, one branch per module, PRs into `main` | |

---

## 8. Repository structure

```
legacylens/
├── CLAUDE.md                 # Short instructions for Claude Code; references this doc
├── docs/
│   └── PROJECT_PLAN.md       # This file
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app, CORS, router registration
│   │   ├── config.py         # Settings (env vars, USE_MOCKS)
│   │   ├── models.py         # SHARED CONTRACTS — source of truth
│   │   ├── api/              # Routers: ingest, risks, interviews, artifacts, ask, graph
│   │   ├── services/
│   │   │   ├── llm.py        # Chat + embeddings (real + mock)
│   │   │   └── search.py     # AI Search wrapper (real + in-memory mock)
│   │   ├── ingestion/        # loaders, normalizer, redactor, chunker, extractor, pipeline
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
│   ├── sample/               # Generated artifacts (JSON)
│   └── graph.json            # Persisted knowledge graph
├── frontend/                 # React + Vite dashboard
├── bot/                      # Teams / Copilot Studio setup notes + config
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
1. **Load** raw sources (synthetic JSON for MVP; Microsoft Graph later).
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

All endpoints must work with `USE_MOCKS=true`.

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
AZURE_SEARCH_INDEX=legacylens-chunks

DATA_DIR=./data
GRAPH_PATH=./data/graph.json

# Stretch
AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
```

---

## 15. Privacy, consent and responsible AI

These are part of the pitch. Judges will ask about them.

- **Explicit consent:** individuals opt in; ingestion is scoped to **project** channels, mailboxes and repos, not personal ones.
- **Least privilege:** Microsoft Graph permissions limited to the project's resources.
- **PII redaction** in the ingestion pipeline.
- **Human review:** interviewees approve captured knowledge before it is published.
- **Transparency:** every generated answer and document carries citations; no uncited claims.
- **No performance surveillance:** risk scores describe *knowledge concentration*, not individual productivity. The dashboard shows topics, not employee rankings.
- **Retention:** raw source data can be purged after knowledge is captured; generated docs remain.


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
| Azure quota / provisioning delays | Mock mode for everything; provision on Day 1 morning |
| LLM output not valid JSON | Structured outputs / JSON mode, Pydantic validation, retry once, fall back to mock |
| Planted risks don't surface | Deterministic dataset + acceptance test on risk ranking |
| Teams integration takes too long | Web UI is the primary demo; Teams is additive |
| Live demo failure | Recorded backup video; cached responses for the demo flow |
| Scope creep | Stick to MVP goals G1–G6; stretch only after demo flow works end to end |

---

## 18. Future roadmap (post-hackathon)

- Live Microsoft Graph connectors with incremental sync.
- Azure DevOps / Jira / GitHub connectors.
- Cosmos DB (or a graph DB) for the knowledge graph at scale.
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
- Commit after each completed step with a clear message.