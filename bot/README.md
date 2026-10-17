# bot/ — LegacyLens Teams app

The Teams app is a thin client: a bot (Microsoft 365 Agents SDK, hosted inside the
FastAPI app at `POST /api/messages`) that calls the existing backend endpoints.
No business logic lives in the bot layer (Plan §6, §21).

## Contents
- `manifest/manifest.template.json` — Teams manifest v1.29. `{{BOT_APP_ID}}` is filled at build time.
  Bot scopes `personal`/`team`/`groupChat`; RSC `ChannelMessage.Read.Group` + `ChatMessage.Read.Chat`.
- `manifest/color.png` (192×192), `manifest/outline.png` (32×32, white on transparent).
- `build/LegacyLens.zip` — generated, gitignored.

## One-time setup (tenant admin)
1. **Allow custom apps** — Teams admin center → Teams apps → Manage apps → Actions →
   Org-wide app settings → *Custom apps* on; Setup policies → Global → *Upload custom apps* on.
   Can take a few hours to apply.
2. **Azure Bot** — portal → Create a resource → *Azure Bot*: resource group `rg-legacylens`,
   handle `legacylens-bot`, pricing **F0**, type **Single Tenant**, *Create new Microsoft App ID*.
   Then Configuration → copy App ID + Tenant ID; Manage → Certificates & secrets → new client
   secret. Put them in `.env` as `BOT_APP_ID`, `BOT_APP_TENANT_ID`, `BOT_APP_PASSWORD`.
   Channels → Microsoft Teams → Agree → Save.
3. **Public endpoint** — `devtunnel create legacylens -a`, `devtunnel port create legacylens -p 8000`,
   `devtunnel host legacylens`. Set `PUBLIC_BASE_URL` in `.env` and the bot's messaging endpoint
   to `<PUBLIC_BASE_URL>/api/messages`.

## Run the bot locally against Teams
```bash
cd backend && ../.venv/bin/uvicorn app.main:app --port 8000   # terminal 1
devtunnel host legacylens                                     # terminal 2
```
Open `<PUBLIC_BASE_URL>/api/messages` in a browser: `{"status":"ok"}` means the tunnel reaches
the bot. In the channel, try `@LegacyLens help`, `@LegacyLens risks`,
`@LegacyLens How did Ravi usually handle gateway timeouts?`.

Code: `backend/app/api/bot.py` (route), `backend/app/bot/agent.py` (SDK wiring, JWT check),
`backend/app/bot/replies.py` (what the bot says; calls the Copilot and risk store). With no
`BOT_APP_ID` the bot runs anonymously for tests and the Agents Playground only.

## Build and install
```bash
.venv/bin/python scripts/build_teams_package.py   # -> bot/build/LegacyLens.zip
```
Teams → Apps → Manage your apps → **Upload an app** → *Upload a custom app* → pick the zip →
**Add to a team** (choose the project channel). Bump `version` in the template before re-uploading.
