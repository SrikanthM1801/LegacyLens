# scripts/

One-shot command-line entry points. Run them with the repo venv:
`../.venv/bin/python scripts/<name>.py`

| Script | Status | Purpose |
| --- | --- | --- |
| `generate_dataset.py` | step 3 | Build the deterministic synthetic "Payments Platform" dataset into `data/sample/` (templated, fixed seed, no LLM needed; `--use-llm` enriches text). |
| `ingest.py` | step 4 | Run the ingestion pipeline over `data/sample/` from the CLI — same code path as `POST /ingest`. |
| `build_teams_package.py` | T31 | Fill the Teams manifest with `BOT_APP_ID` and zip it with icons into `bot/build/LegacyLens.zip` for sideloading. |
| `scan_project_chats.py` | T31g | Invite LegacyLens into project members' existing group chats (same rules as new chats; consent asked in each). |
| `show_audit.py` | T31k | Print the audit trail from Azure Blob (`--hours`, `--action`) or the SharePoint file inventory (`--files`). |
