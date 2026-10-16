"""
LLM service: chat, JSON-mode chat and embeddings.

`LLMService` is the one interface agents and ingestion depend on. `MockLLM` is
deterministic and offline; `AzureLLM` talks to Azure OpenAI. Use `get_llm()` to
pick one from `settings.use_mocks` — never instantiate either directly elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import time
from functools import lru_cache
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_TOKEN_RE = re.compile(r"[a-z0-9_\-]+")
_EMBED_BATCH = 64  # inputs per embeddings request; the 10K TPM limit is the real cap


class LLMService(Protocol):
    model_name: str

    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str: ...

    def chat_json(self, system: str, user: str, schema: type[T]) -> T:
        """Chat in JSON mode, validate against `schema`, retry once on bad output."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def hash_embed(text: str, dim: int) -> list[float]:
    """Deterministic bag-of-words hashing embedding, L2-normalised.

    Texts sharing words get high cosine similarity, which is enough for the mock
    search to return sensible hits offline.
    """
    vec = [0.0] * dim
    for tok in tokenize(text):
        h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class MockLLM:
    model_name = "mock"

    def __init__(self, settings: Settings) -> None:
        self._dim = settings.embedding_dim

    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str:
        if json_mode:
            return "{}"
        first = next((ln.strip() for ln in user.splitlines() if ln.strip()), "")
        return f"[mock answer] Based on the provided context: {first[:200]}"

    def chat_json(self, system: str, user: str, schema: type[T]) -> T:
        # Agents provide richer mock behaviour; here we return schema defaults.
        return schema.model_validate({})

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embed(t, self._dim) for t in texts]


class AzureLLM:
    def __init__(self, settings: Settings) -> None:
        from openai import AzureOpenAI

        self._s = settings
        self.model_name = settings.azure_openai_chat_deployment
        self._client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            timeout=60.0,
            max_retries=0,  # we handle 429 ourselves so we can honour Retry-After
        )

    def _with_retry(self, fn, *, attempts: int = 5):
        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        for i in range(attempts):
            try:
                return fn()
            except RateLimitError as e:
                retry_after = getattr(e.response, "headers", {}).get("retry-after")
                delay = float(retry_after) if retry_after else 2**i
            except (APIConnectionError, APITimeoutError, InternalServerError):  # transient: network, 5xx
                delay = 2**i
            if i == attempts - 1:
                raise
            time.sleep(delay + random.random())

    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = self._with_retry(
            lambda: self._client.chat.completions.create(
                model=self._s.azure_openai_chat_deployment,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self._s.llm_temperature,
                max_tokens=self._s.llm_max_tokens,
                **kwargs,
            )
        )
        return resp.choices[0].message.content or ""

    def chat_json(self, system: str, user: str, schema: type[T]) -> T:
        # JSON mode requires the word "JSON" in the prompt.
        sys_prompt = f"{system}\n\nRespond with a single valid JSON object."
        last_err: Exception | None = None
        for _ in range(2):
            raw = self.chat(sys_prompt, user, json_mode=True)
            try:
                return schema.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                log.warning("LLM returned invalid JSON, retrying: %s", e)
        raise ValueError(f"LLM did not return valid {schema.__name__}: {last_err}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i : i + _EMBED_BATCH]
            resp = self._with_retry(
                lambda b=batch: self._client.embeddings.create(
                    model=self._s.azure_openai_embedding_deployment, input=b
                )
            )
            out.extend(d.embedding for d in resp.data)
        return out


@lru_cache
def get_llm() -> LLMService:
    s = get_settings()
    if s.use_mocks or not s.azure_openai_configured:
        if not s.use_mocks:
            log.warning("USE_MOCKS=false but Azure OpenAI is not configured; using mock LLM")
        return MockLLM(s)
    return AzureLLM(s)
