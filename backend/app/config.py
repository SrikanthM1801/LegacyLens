"""
Settings for LegacyLens, read from environment / .env (see .env.example).

The one flag everyone cares about is USE_MOCKS. With USE_MOCKS=true (the
default) the whole app runs with no Azure keys: services/llm.py and
services/search.py fall back to deterministic in-process implementations.
Set USE_MOCKS=false once real Azure values are filled in.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repo root — backend/app/config.py -> backend/app -> backend -> <root>
REPO_ROOT = Path(__file__).resolve().parents[2]

VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- mode -------------------------------------------------------------
    use_mocks: bool = True

    # --- Azure OpenAI (Azure AI Foundry) ----------------------------------
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"

    # --- Azure AI Search --------------------------------------------------
    azure_search_endpoint: str = ""
    azure_search_api_key: str = ""
    azure_search_index_name: str = "legacylens-chunks"

    #: Must match the embedding deployment's output size
    #: (text-embedding-3-small = 1536, -3-large = 3072).
    embedding_dim: int = 1536

    # --- LLM behaviour ----------------------------------------------------
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1200

    #: Load the synthetic dataset from data_dir at startup. Tests and offline demos keep this on;
    #: a live deployment sets USE_SAMPLE_DATA=false so only real sources (SharePoint, Teams) are known.
    use_sample_data: bool = True

    # --- paths ------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data" / "sample"
    graph_path: Path = REPO_ROOT / "data" / "graph.json"
    state_dir: Path = REPO_ROOT / "data" / ".state"

    #: Comma-separated org email domains kept as-is; all other addresses are redacted.
    org_email_domains: str = "contoso-payments.com"

    # --- ingestion tuning -------------------------------------------------
    chunk_size_chars: int = 900
    chunk_overlap_chars: int = 150

    # --- Teams bot (Azure Bot Service, single-tenant Entra app) ------------
    bot_app_id: str = ""
    bot_app_password: str = ""
    bot_app_tenant_id: str = ""
    bot_app_type: str = "SingleTenant"
    #: Public HTTPS base (dev tunnel / App Service); bot endpoint is <base>/api/messages.
    public_base_url: str = ""
    #: Teams app manifest id (bot/manifest/manifest.template.json "id").
    teams_app_manifest_id: str = "c0e23221-8df7-42a5-ae6d-122127e3cb7b"

    # --- Live Microsoft 365 (Graph + Azure Tables) --------------------------
    #: Separate from USE_MOCKS: real Graph calls, Graph subscriptions and Azure Table
    #: storage only run when LIVE_M365=true. Off by default so tests stay offline.
    live_m365: bool = False
    #: Graph app credentials; blank = reuse the bot's Entra app (BOT_APP_*).
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    #: Random secret Graph echoes on every notification; we reject mismatches.
    graph_webhook_client_state: str = ""
    #: Org-catalog id of the LegacyLens app; looked up from the manifest id when blank.
    teams_catalog_app_id: str = ""
    #: A new group chat is "a project chat" when at least this many members are on the project team.
    project_chat_min_members: int = 2
    #: Messages to backfill from a conversation right after consent.
    backfill_messages: int = 50

    azure_tables_account_url: str = ""
    azure_storage_account_url: str = ""
    azure_storage_container: str = "knowledge"
    #: Append-blob audit trail for the admin page (events/YYYY/MM/DD/HH.jsonl).
    audit_container: str = "audit"
    #: Required as X-Admin-Key on /audit. Blank = /audit only answers with LIVE_M365=false.
    admin_api_key: str = ""
    azure_language_endpoint: str = ""
    #: Project SharePoint site, e.g. https://contoso.sharepoint.com/sites/LegacyLensProject (or a site id).
    sharepoint_site_url: str = ""
    #: How often to pull new / changed / deleted files from the site.
    sharepoint_sync_minutes: int = 5

    # --- Dashboard (org -> account -> project, over the audit trail) -----------
    org_name: str = "Organisation"
    #: Account used for projects without an entry in PROJECT_ACCOUNTS.
    default_account_name: str = "Default account"
    #: JSON object mapping a project (Teams team name or team id) to its client account,
    #: e.g. {"Payments Platform": "Contoso", "Storefront": "Northwind"}. Blank = one default account.
    project_accounts: str = ""
    #: Offline only (LIVE_M365=false): fill the in-memory audit trail with deterministic demo data.
    dashboard_demo: bool = False

    #: The built dashboard (`npm run build` in frontend/). When it exists the backend serves it at /app,
    #: so one public URL (e.g. the dev tunnel) gives the API and the UI together, with no CORS.
    ui_dist_dir: Path = REPO_ROOT / "frontend" / "dist"

    # --- API --------------------------------------------------------------
    #: Comma-separated (CORS_ORIGINS=http://a,http://b). Kept as a plain string
    #: because pydantic-settings JSON-decodes list fields from env before validators run.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def account_by_project(self) -> dict[str, str]:
        """PROJECT_ACCOUNTS parsed; keys lower-cased. A malformed value means 'no mapping', never a crash."""
        import json

        try:
            raw = json.loads(self.project_accounts) if self.project_accounts.strip() else {}
        except ValueError:
            return {}
        return {str(k).strip().lower(): str(v).strip() for k, v in raw.items()} if isinstance(raw, dict) else {}

    @property
    def org_domains(self) -> frozenset[str]:
        return frozenset(d.strip().lower() for d in self.org_email_domains.split(",") if d.strip())

    @property
    def azure_openai_configured(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key)

    @property
    def graph_credentials(self) -> tuple[str, str, str]:
        """(tenant, client id, secret) for app-only Graph calls; falls back to the bot app."""
        return (
            self.graph_tenant_id or self.bot_app_tenant_id,
            self.graph_client_id or self.bot_app_id,
            self.graph_client_secret or self.bot_app_password,
        )

    @property
    def azure_search_configured(self) -> bool:
        return bool(self.azure_search_endpoint and self.azure_search_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. FastAPI deps and scripts both use this."""
    return Settings()


settings = get_settings()
