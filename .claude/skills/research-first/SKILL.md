---
name: research-first
description: Search the web for current docs before using or writing code against any library, SDK, Azure service, API, model name, or version. Use at the start of any task that touches an external dependency, a new technique, or an error message you can't explain from the repo.
---

# Research first

Training knowledge goes stale, and our pins (`openai==1.57.0`, `azure-search-documents==11.5.2`, FastAPI, Vite, etc.) may be outdated or have changed APIs. Do not code external integrations from memory.

## When to search
- Before writing code that calls Azure OpenAI, Azure AI Search, Microsoft Graph, Copilot Studio, M365 Agents SDK, Azure AI Speech, or any pip/npm package.
- Before choosing a library, model, SKU, API version, or chunking/retrieval technique.
- When an error or deprecation warning appears. Search the exact message first.
- When the user asks "best way to…", "latest…", or anything time-sensitive.

## How
1. Use WebSearch (standard first; extended if the result is thin, outdated, or for niche/very recent topics). Send independent searches in one turn.
2. Prefer official sources: learn.microsoft.com, the library's GitHub README/releases, PyPI/npm pages. Use WebFetch to read the actual page rather than trusting a snippet.
3. Check the version and date on what you find. Match it against `backend/requirements.txt` and `frontend/package.json`.
4. If the docs contradict the repo's pinned version, tell the user and propose the bump; don't silently upgrade.
5. Write what you learned into a short code comment or `Plan.md`/`AzureResources.md` only if it affects the team (gotchas, limits, breaking changes). Cite the URL.

## Output
State in one line what you looked up and the source, then proceed. Skip the search only for pure repo-internal work (editing our own code, tests, refactors) and say so if asked.

Never put secrets, keys, `.env` content, or real project data into a search query.
