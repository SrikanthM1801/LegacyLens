# scripts/

One-shot command-line entry points. Run them with the repo venv:
`../.venv/bin/python scripts/<name>.py`

| Script | Status | Purpose |
| --- | --- | --- |
| `generate_dataset.py` | step 3 | Build the deterministic synthetic "Payments Platform" dataset into `data/sample/` (templated, fixed seed, no LLM needed; `--use-llm` enriches text). |
| `ingest.py` | step 4 | Run the ingestion pipeline over `data/sample/` from the CLI — same code path as `POST /ingest`. |
