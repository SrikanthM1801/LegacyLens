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

## Consent and project group chats (live Microsoft 365)

Flow: LegacyLens is added to a channel or group chat -> it posts **Allow / Not now** -> nothing is
stored until Allow -> on Allow it backfills the last `BACKFILL_MESSAGES` messages and captures new
ones -> `@LegacyLens pause` stops, `@LegacyLens resume` asks again. Consent records live in the
`ConsentRecords` table; the project roster (members of the team LegacyLens is installed in) in `SyncState`.

**Automatic project chats:** Graph tells `POST /webhooks/graph` whenever a chat is created. A group
chat with >= `PROJECT_CHAT_MIN_MEMBERS` (2) project members gets LegacyLens installed, and the bot
asks for consent in that chat. *Not now* uninstalls it again. 1:1 chats are never touched.

One-time setup (tenant admin):
1. Entra admin center -> App registrations -> the bot app (`BOT_APP_ID`) -> API permissions ->
   Add -> Microsoft Graph -> **Application**: `Chat.ReadBasic.All`,
   `TeamsAppInstallation.ReadWriteAndConsentSelfForChat.All`, `AppCatalog.Read.All` ->
   **Grant admin consent**.
2. `.env`: `LIVE_M365=true`, `GRAPH_WEBHOOK_CLIENT_STATE=<random>`, `PUBLIC_BASE_URL=<tunnel>`,
   `AZURE_TABLES_ACCOUNT_URL=...`. Run `az login` (Tables use your Entra identity, no keys).
3. Restart uvicorn: the log shows `graph chat subscription active`. Re-install or `@LegacyLens resume`
   in the project channel so the roster is recorded.

Chats created before the roster was recorded (or while the server was down) are not re-notified
by Graph; run `.venv/bin/python scripts/scan_project_chats.py` to check project members' recent
group chats with the same rules.

Guests: project members who are guests must **switch to this organisation in Teams** before creating
the chat. A chat created from their home tenant is a cross-tenant chat we can't see or join.
