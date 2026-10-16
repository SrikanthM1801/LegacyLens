# LegacyLens — Task Board

One small, independently shippable task per row. Work them **in order** with `/next-task` (or `/next-task T07` for a specific one). Each task must leave `main` green: `cd backend && ../.venv/bin/python -m pytest` passes in `USE_MOCKS=true`.

Status: `[ ]` todo · `[~]` in progress · `[x]` done · `[S]` stretch

Demo path (what matters most): **T12 ingest → T17 risks → T21 interview+approve → T22 runbook → T25 ask → T27/T28 UI.** Everything else supports it.

---

## Phase 0 — Foundation
- [x] **T00 Config + contracts** — `config.py`, `models.py` (+ `is_departing`, `risk_level`, interviews count as docs).
- [x] **T01 App skeleton + mock services** — `main.py` `/health`, `services/llm.py`, `services/search.py`, 4 tests.
- [x] **T02 Azure smoke script** — `scripts/smoke_azure.py`: with `USE_MOCKS=false`, one chat call, one embedding call, create index, upsert + search one chunk, print latency. Never print keys. *Done when:* script passes against real Azure, or fails with a clear message when keys are missing. *Deps:* none. *Search the web for current SDK usage.*

## Phase 1 — Synthetic dataset (`scripts/generate_dataset.py`, deterministic, seed fixed, no LLM)
- [x] **T03 People + tickets** — `people.json` (Ravi departing, Priya, Arjun, Meera); ~20 tickets incl. INC-142/155/171/188 (gateway, resolved by Ravi) and settlement incidents (Arjun). *Done when:* files validate as `Person`/`Artifact`.
- [x] **T04 Emails + chats** — ~60 messages. Gateway fix lives only in chats; `--skip-recon` workaround only in Arjun's chats; Kafka-vs-RabbitMQ only in Priya's emails. Include noise.
- [x] **T05 Wiki + commits** — ~6 docs (release process, onboarding, API auth; **none** for gateway/settlement/Kafka) and ~40 commits (Ravi → gateway, Arjun → settlement, others spread).
- [x] **T06 Dataset tests** — re-run gives byte-identical output; planted facts present; every record validates against `models.py`. *Deps:* T03–T05.

## Phase 2 — Ingestion & graph
- [x] **T07 Loaders + normalizer** — `ingestion/loaders.py`: read `data/sample/*.json` → `list[Artifact]`, de-dupe on `source_id`.
- [x] **T08 PII redactor** — `ingestion/redactor.py`: phone numbers, external emails, card numbers → placeholders; pure function + tests.
- [x] **T09 Chunker** — `ingestion/chunker.py`: `Artifact` → `Chunk`s (900/150 chars, sentence-aware), carries title/author/type/timestamp.
- [x] **T10 GraphStore** — `graph/store.py` interface + `graph/networkx_store.py`: add/get nodes & edges, neighbors, stats, save/load `graph.json`, weights accumulate with evidence ids.
- [x] **T11 Entity/relation extractor** — `ingestion/extractor.py`: LLM structured extraction (prompt in `agents/prompts.py`) with a **rule-based mock** (ticket ids, known system aliases, authorship, commit paths) that detects the planted topics. Alias resolution ("PG"/"gateway" → one System). *Deps:* T07, T10.
- [x] **T12 Pipeline + `POST /ingest`** — `ingestion/pipeline.py` (load → redact → chunk → embed → extract → upsert), `api/ingest.py`, `scripts/ingest.py`, `/health` shows real counts. Cache by content hash. *Deps:* T07–T11.
- [x] **T13 `GET /graph` + `GET /people`** — graph JSON for visualization; people with knowledge-concentration summary (topics, not rankings).

## Phase 3 — Risk analysis
- [x] **T14 Activity + bus factor** — `agents/gap_analyzer.py`: weights RESOLVED 5 / DECIDED 4 / COMMITTED_TO 3 / authored doc 2 / mention 1, recency boost; bus factor (≥15% share), single-person flag (≥70%). Pure functions + tests.
- [x] **T15 Coverage score** — search restricted to `DOC_ARTIFACT_TYPES` + `DOCUMENTED_BY` edges → 0..1.
- [x] **T16 Risk score + ranking + acceptance test** — formula, ×1.5 for departing owner, `risk_level`, evidence citations. *Done when:* the 3 planted risks rank top 3 and documented topics rank low.
- [x] **T17 `GET /risks`, `/risks/{id}`** — reads cached results; recompute on ingest/approval.

## Phase 4 — Interviews
- [x] **T18 Question generator** — `agents/interviewer.py`: 3–5 evidence-quoting questions (triggers, first checks, criteria, workarounds, gotchas, escalation); mock produces deterministic grounded questions.
- [x] **T19 Session store + start/get** — `POST /interviews/{risk_id}/start`, `GET /interviews/{session_id}`; interviewee = top owner, prefer departing.
- [x] **T20 Answer endpoint + re-ingest** — `POST /interviews/{id}/answer`: store answer, create `Artifact(type=interview)`, chunk + index, optional vague-answer follow-up.
- [x] **T21 Approve + risk recompute** — `POST /interviews/{id}/approve`: add `DOCUMENTED_BY` edges, recompute risk. *Done when:* risk score visibly drops (red → green) in a test.

## Phase 5 — Generated documents
- [x] **T22 Runbook generator** — `agents/generator.py` + `POST /artifacts/runbook`: sections per Plan §11.4; every step cites an answer/artifact.
- [x] **T23 Handover pack** — `POST /artifacts/handover`: systems owned, open risks, decisions, contacts, runbook links, unresolved questions.
- [ ] **T24 Decision log** `[S]` — `POST /artifacts/decision-log`.

## Phase 6 — Copilot
- [ ] **T25 `POST /ask`** — `agents/copilot.py`: hybrid retrieval + graph expansion, answer only from context, citations on every claim, "not enough info → suggest interview", person-centric queries.

## Phase 7 — Frontend (React + Vite + TS)
- [ ] **T26 Scaffold + typed API client** — Vite app, one `api.ts`, types mirroring `models.py`, layout, loading/error components. Use `/ui-ux-pro-max:ui-ux-pro-max` for design.
- [ ] **T27 Risks dashboard** — ranked table/heatmap, risk level colors, bus factor, coverage, evidence drawer.
- [ ] **T28 Interview page** — start, one question at a time, evidence shown, answer, review & approve, live score change.
- [ ] **T29 Ask page** — question box, cited answer, confidence, follow-ups.
- [ ] **T30 Documents page** — generate/view runbook & handover markdown with citations; download.

## Phase 8 — Teams
- [ ] **T31 Teams Project Copilot** — Copilot Studio (preferred) or M365 Agents SDK calling `POST /ask`; document setup in `bot/README.md`. *Search the web for current setup steps.*

## Phase 9 — Polish & demo
- [ ] **T32 Demo hardening** — run `/demo-check`, cache demo responses, seed script for a one-command reset, backup recording plan.
- [ ] **T33 README + pitch notes** — real README, architecture diagram, responsible-AI slide notes (consent, redaction, human approval, no surveillance).
- [ ] **T34 Security + quality pass** — `/security-review`, `/simplify`, `/code-review`; secrets audit; dependency check.

## Stretch
- [S] **T35 Graph visualization page.**
- [S] **T36 Voice interviews (Azure AI Speech).**
- [S] **T37 Live Microsoft Graph ingestion (test tenant).**
- [S] **T38 Deploy backend + frontend to Azure.**
