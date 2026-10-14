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

from pydantic import Field, field_validator
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

    # --- paths ------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data" / "sample"
    graph_path: Path = REPO_ROOT / "data" / "graph.json"
    state_dir: Path = REPO_ROOT / "data" / ".state"

    # --- ingestion tuning -------------------------------------------------
    chunk_size_chars: int = 900
    chunk_overlap_chars: int = 150

    # --- API --------------------------------------------------------------
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Allow CORS_ORIGINS=http://a,http://b in .env (not just JSON)."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @property
    def azure_openai_configured(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key)

    @property
    def azure_search_configured(self) -> bool:
        return bool(self.azure_search_endpoint and self.azure_search_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. FastAPI deps and scripts both use this."""
    return Settings()


settings = get_settings()
