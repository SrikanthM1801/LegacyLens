# LegacyLens — Azure Resource Setup

## Summary

All core Azure resources for LegacyLens are provisioned and in the Succeeded state: one resource group, one Azure OpenAI account with two model deployments, and one Azure AI Search service. Azure OpenAI is in eastus2. Search landed in eastus because eastus2 had no Search capacity.

- **Tenant:** `xxxxx-xxxxx`
- **Subscription:** `xxxxxx`
- **Provisioned:** 15 Oct 2026, roughly 00:45–01:10 IST, via Azure CLI
- **Not yet done:** `.env` file, connectivity smoke test, search index (created by code, not by hand)

## Pre-flight checks

No blockers were found: both providers are registered, Azure OpenAI is enabled in eastus2, and no policy restricts regions or SKUs.

| Check | Command | Result |
| --- | --- | --- |
| Cognitive Services provider | `az provider show -n Microsoft.CognitiveServices` | Registered |
| Search provider | `az provider show -n Microsoft.Search` | Registered |
| Azure OpenAI SKU in eastus2 | `az cognitiveservices account list-skus --kind OpenAI --location eastus2` | OpenAI S0 available |
| Policy assignments | `az policy assignment list` | CloudLabs Tag Replication v.001; ASC Default. Neither restricts regions or SKUs |
| Local (key) auth | `disableLocalAuth` on the OpenAI account | null, so API keys work |

## Provisioning log

Every step succeeded except Search in eastus2, which failed on capacity three times before an eastus attempt worked. Steps are in run order.

| # | Step | Command (abridged) | Outcome |
| --- | --- | --- | --- |
| 0 | Re-authenticate (token from 2026-02-01 had expired) | `az login --tenant xxxx` | Done |
| 1 | Create resource group | `az group create --name rg-legacylens --location eastus2` | Succeeded |
| 2 | Create Azure OpenAI account | `az cognitiveservices account create --name legacylens-aoai --kind OpenAI --sku S0 --location eastus2 --custom-domain legacylens-aoai` | Succeeded, 19:18 UTC |
| 3 | List deployable models | `az cognitiveservices account list-models …` | gpt-4o 2024-05-13 / 2024-08-06 / 2024-11-20; text-embedding-3-small v1; text-embedding-3-large v1 |
| 4 | Deploy chat model | `deployment create --deployment-name gpt-4o --model-version 2024-11-20 --sku-name GlobalStandard --sku-capacity 30` | Succeeded, 19:22 UTC |
| 5 | Deploy embedding model | `deployment create --deployment-name text-embedding-3-small --model-version 1 --sku-name Standard --sku-capacity 10` | Succeeded, 19:25 UTC |
| 6 | Search, Free SKU, eastus2 | `az search service create … --sku free --location eastus2` | Failed: InsufficientResourcesAvailable |
| 7 | Search, Basic SKU, eastus2 (2 tries) | `az search service create … --sku basic --location eastus2` | Failed: InsufficientResourcesAvailable |
| 8 | Search, Basic SKU, region-probe loop | loop over eastus, westus2, westus3, … | Created `legacylens-search` in eastus on first region |

## Resource inventory

Five resources exist, all in `rg-legacylens`.

| Resource | Name | SKU / version | Capacity | Region | Status |
| --- | --- | --- | --- | --- | --- |
| Resource group | `rg-legacylens` | — | — | eastus2 | Succeeded |
| Azure OpenAI account | `legacylens-aoai` | S0 | — | eastus2 | Succeeded |
| Chat deployment | `gpt-4o` | gpt-4o 2024-11-20, GlobalStandard | 30 (30K TPM) | eastus2 | Succeeded |
| Embedding deployment | `text-embedding-3-small` | v1, Standard | 10 (10K TPM) | eastus2 | Succeeded |
| Azure AI Search | `legacylens-search` | Basic, 1 partition, 1 replica | — | eastus | Created |

The search index `legacylens-chunks` is deliberately not created by hand. Ingestion code creates it so the schema matches the Chunk model (1536-dim vector field, filterable artifact\_type, author, timestamp).

## .env key values

Eight of the ten values are known now; the two API keys must be fetched with the commands below and pasted only into `.env`, which is gitignored.

| .env key | Value | Source |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | `https://legacylens-aoai.openai.azure.com/` | Account create output |
| `AZURE_OPENAI_API_KEY` | secret — fetch | `az cognitiveservices account keys list --name legacylens-aoai -g rg-legacylens --query key1 -o tsv` |
| `AZURE_OPENAI_API_VERSION` | `2024-10-21` | Already in `.env.example` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-4o` | Deployment name |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-small` | Deployment name |
| `AZURE_SEARCH_ENDPOINT` | `https://legacylens-search.search.windows.net` | Service name |
| `AZURE_SEARCH_API_KEY` | secret — fetch | `az search admin-key show --service-name legacylens-search -g rg-legacylens --query primaryKey -o tsv` |
| `AZURE_SEARCH_INDEX_NAME` | `legacylens-chunks` | Already in `.env.example` |
| `EMBEDDING_DIM` | `1536` | Fixed by text-embedding-3-small |
| `USE_MOCKS` | `true` | Keep `true` on main; set `false` only for local real-call tests |

Other identifiers for reference: resource group `rg-legacylens`, chat model version `2024-11-20`, embedding model version `1`.

## Data storage and PII resources (17 Oct 2026)

Created for live Microsoft 365 data. Both are **keyless**: account keys are disabled, and access is through Entra ID roles only (your `az login` locally, a managed identity once deployed). Nothing here holds raw conversations; see Plan §15.

| Resource | Name | SKU | Region | Security settings |
| --- | --- | --- | --- | --- |
| Storage account | `legacylensdata` | StorageV2, Standard_LRS | eastus2 | Shared-key access **off**, public blob access off, HTTPS only, TLS 1.2, blob + container soft delete 7 days, encrypted at rest by default |
| ↳ Blob container | `knowledge` | — | — | Knowledge graph and app state (redacted content only) |
| ↳ Blob container | `audit` | — | — | Audit trail for the admin page: append blobs `events/YYYY/MM/DD/HH.jsonl`, one event per line (ids, names, counts; never message text) |
| ↳ Tables | `ConsentRecords`, `SyncState`, `AuditLog` | — | — | Consent per conversation/mailbox/site; project roster + sync cursors. `AuditLog` table is unused (audit moved to the `audit` blob container) and can be deleted |
| Azure AI Language | `legacylens-language` | F0 (free, 5K text records/month) | eastus2 | Custom domain, `disableLocalAuth=true`; used for PII detection/redaction before indexing |

Role assignments (scoped to each resource, signed-in user): Storage Blob Data Contributor, Storage Table Data Contributor, Cognitive Services User. The deployed app's managed identity needs the same three roles.

Verified: key-based storage calls are rejected ("not permitted on this storage account"); a keyless PII call redacted a phone number and a personal email address.

## Rate limits, decisions and gotchas

The embedding deployment is the tightest limit at 10K tokens per minute, so ingestion should batch inputs and retry on HTTP 429.

| Deployment | Tokens / min | Requests | Notes |
| --- | --- | --- | --- |
| `gpt-4o` | 30,000 | 30 per 10 s | Version auto-upgrades when a new default ships |
| `text-embedding-3-small` | 10,000 | 10 per 10 s | Up to 2,048 inputs per request; deployed at 10, not the planned 30 |

- **Region split:** OpenAI in eastus2, Search in eastus. Works as-is; only adds slight latency on index upserts.
- **Embedding model:** use text-embedding-3-small only. text-embedding-3-large is deployable but 3072-dim, which breaks the 1536-dim config and index schema.
- **Search SKU:** Basic, about $0.10/hour. It cannot be paused, only deleted. Includes the semantic ranker.
- **Auth:** key-based auth is enabled (`disableLocalAuth` is null), so no Entra ID changes are needed in services/llm.py or services/search.py.
- **Deferred:** Azure AI Speech and the Microsoft Graph app registration are stretch items; Graph needs tenant admin consent.

## Next steps and teardown

The next step is to create `.env` and confirm both services answer before any ingestion code runs.

- [ ] `cp .env.example .env` and fill in the two API keys
- [ ] Write services/llm.py and services/search.py plus a connectivity smoke script
- [ ] Run the smoke script with `USE_MOCKS=false` locally, then set it back to `true`
- [ ] Build the dataset generator (step 3 of the build)
- [ ] After the hackathon: `az group delete --name rg-legacylens --yes` to stop Basic Search billing