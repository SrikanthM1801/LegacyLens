"""
Connectivity smoke test for the real Azure services (T02).

Run from the repo root:  .venv/bin/python scripts/smoke_azure.py

Checks, in order: config present -> chat completion -> embedding (dimension) ->
create index -> upsert one chunk -> search it back. Prints latency per step and
never prints keys. Exits non-zero on the first failure.

NOTE: the index step recreates the index (`reset`), so it wipes any indexed data.
Point AZURE_SEARCH_INDEX_NAME at a throwaway name if you have already ingested.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ["USE_MOCKS"] = "false"  # this script only makes sense against real services

from app.config import get_settings  # noqa: E402
from app.models import ArtifactType, Chunk  # noqa: E402
from app.services.llm import AzureLLM  # noqa: E402
from app.services.search import AzureSearch  # noqa: E402


def step(name: str, fn):
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception as e:  # type + message only; SDK errors don't echo keys
        print(f"FAIL  {name}: {type(e).__name__}: {str(e)[:300]}")
        sys.exit(1)
    print(f"ok    {name} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    return result


def main() -> None:
    s = get_settings()
    required = {
        "AZURE_OPENAI_ENDPOINT": s.azure_openai_endpoint,
        "AZURE_OPENAI_API_KEY": s.azure_openai_api_key,
        "AZURE_SEARCH_ENDPOINT": s.azure_search_endpoint,
        "AZURE_SEARCH_API_KEY": s.azure_search_api_key,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"FAIL  config: missing {', '.join(missing)} (fill them in .env, see AzureResources.md)")
        sys.exit(1)
    print(
        f"ok    config (chat={s.azure_openai_chat_deployment}, "
        f"embed={s.azure_openai_embedding_deployment}, index={s.azure_search_index_name})"
    )

    llm = AzureLLM(s)
    reply = step("chat completion", lambda: llm.chat("You are terse.", "Reply with the word: pong"))
    print(f"      reply: {reply.strip()[:60]!r}")

    emb = step("embedding", lambda: llm.embed(["payment gateway timeout"]))
    if len(emb[0]) != s.embedding_dim:
        print(f"FAIL  embedding dim {len(emb[0])} != EMBEDDING_DIM {s.embedding_dim}")
        sys.exit(1)

    search = AzureSearch(s, llm)
    step("create index", search.reset)
    chunk = Chunk(
        id="smoke_1",
        artifact_id="art_smoke",
        artifact_type=ArtifactType.DOC,
        text="Restart the settlement worker when the bank file is late.",
        title="smoke",
    )
    step("upsert chunk", lambda: search.upsert([chunk]))

    # Azure Search indexing is eventually consistent; poll briefly.
    hits = []
    for _ in range(10):
        hits = search.search("settlement worker bank file", top_k=1)
        if hits:
            break
        time.sleep(1)
    if not hits or hits[0].artifact_id != "art_smoke":
        print("FAIL  search: smoke chunk not returned within 10s")
        sys.exit(1)
    print(f"ok    search (score {hits[0].score})")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
