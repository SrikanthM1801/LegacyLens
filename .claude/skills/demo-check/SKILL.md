---
name: demo-check
description: Verify the end-to-end LegacyLens demo flow and the planted-risk acceptance check in mock mode. Use before merging to main, before demo rehearsal, and after any change to the dataset, ingestion, analyzer, interviewer, generator or copilot.
---

# Demo check (mock mode, `USE_MOCKS=true`)

Run in order; stop and fix at the first failure.

1. `cd backend && pytest` passes.
2. Regenerate data (`python scripts/generate_dataset.py`) is deterministic: re-running yields no diff in `data/sample/`.
3. `POST /ingest` returns non-zero artifacts, chunks, entities, edges and no unexpected warnings.
4. `GET /risks`: the 3 planted risks are in the top 3 — payment gateway outages (Ravi), settlement batch job (Arjun), Kafka vs RabbitMQ decision (Priya). Well-documented topics (release process, onboarding, API auth) rank low.
5. `POST /interviews/{risk_id}/start`: 3–5 questions, each citing concrete evidence.
6. Answer each question, then approve: the risk's coverage rises and its `risk_score` drops on the next `GET /risks`.
7. `POST /artifacts/runbook` and `/artifacts/handover` return markdown where every step has a citation.
8. `POST /ask` ("How did Ravi usually handle gateway timeouts?") returns a cited answer; an unanswerable question says so and suggests an interview.
9. Frontend: Risks, Interview and Ask pages load with no console errors; the score visibly changes after approval.

Report a pass/fail line per step. If a step fails, show the actual output.
