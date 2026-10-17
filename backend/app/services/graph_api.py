"""
Microsoft Graph client for live Microsoft 365 data, plus a mock with the same interface.

App-only (client credentials) calls with the bot's Entra app unless GRAPH_* overrides it.
Only what the consent flow needs: read a new chat's type and members, install / uninstall
LegacyLens in a chat, read recent messages after consent, and manage the `/chats`
subscription that tells us when a chat is created. Permissions (admin-consented):
  Chat.ReadBasic.All                                  — chat type + members, no messages
  TeamsAppInstallation.ReadWriteAndConsentSelfForChat.All — install ourselves with RSC
  AppCatalog.Read.All                                 — find our catalog id (or set TEAMS_CATALOG_APP_ID)
Chat.ReadBasic.All also lets `scan` list project members' existing group chats (names + members).
Message reads use the RSC grants (ChatMessage.Read.Chat / ChannelMessage.Read.Group) the
chat or team gave the app at install. Never logs tokens or message bodies.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional, Protocol

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
RSC_FOR_CHAT = [{"permissionValue": "ChatMessage.Read.Chat", "permissionType": "Application"}]
TIMEOUT = httpx.Timeout(20.0)


@dataclass
class ChatInfo:
    id: str
    chat_type: str                                 # oneOnOne | group | meeting
    topic: str = ""
    member_aad_ids: list[str] = field(default_factory=list)
    member_names: dict[str, str] = field(default_factory=dict)


@dataclass
class GraphMessage:
    id: str
    created: datetime
    author_aad_id: str
    author_name: str
    html: str


class GraphError(RuntimeError):
    def __init__(self, status: int, what: str):
        super().__init__(f"Graph {what} failed with HTTP {status}")
        self.status = status


class GraphService(Protocol):
    async def get_chat(self, chat_id: str) -> ChatInfo: ...
    async def install_app_in_chat(self, chat_id: str) -> None: ...
    async def uninstall_app_from_chat(self, chat_id: str) -> None: ...
    async def chat_messages(self, chat_id: str, top: int) -> list[GraphMessage]: ...
    async def channel_messages(self, team_id: str, channel_id: str, top: int) -> list[GraphMessage]: ...
    async def recent_group_chat_ids(self, user_aad_id: str, top: int = 20) -> list[str]: ...
    async def ensure_chat_subscription(self, notification_url: str, client_state: str) -> str: ...


def _parse_messages(items: list[dict]) -> list[GraphMessage]:
    out = []
    for m in items:
        user = ((m.get("from") or {}).get("user") or {})
        if m.get("messageType") != "message" or not user.get("id") or m.get("deletedDateTime"):
            continue  # skip system events, bot posts and deleted messages
        out.append(GraphMessage(
            id=m["id"],
            created=datetime.fromisoformat(m["createdDateTime"].replace("Z", "+00:00")),
            author_aad_id=user["id"],
            author_name=user.get("displayName") or "",
            html=(m.get("body") or {}).get("content") or "",
        ))
    return out


class AzureGraph:
    def __init__(self, s: Settings) -> None:
        import msal

        tenant, client_id, secret = s.graph_credentials
        self._s = s
        self._app = msal.ConfidentialClientApplication(
            client_id, client_credential=secret, authority=f"https://login.microsoftonline.com/{tenant}"
        )
        self._catalog_id: Optional[str] = s.teams_catalog_app_id or None

    async def _token(self) -> str:
        result = await asyncio.to_thread(
            self._app.acquire_token_for_client, scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise GraphError(401, f"token ({result.get('error')})")
        return result["access_token"]

    async def _call(self, method: str, path: str, what: str, json: Any = None) -> dict:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for attempt in range(3):
                r = await client.request(method, path if path.startswith("http") else GRAPH + path,
                                         headers=headers, json=json)
                if r.status_code in (429, 503, 504) and attempt < 2:
                    await asyncio.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                    continue
                break
        if r.status_code >= 400:
            code = (r.json().get("error", {}) if r.content else {}).get("code", "")
            log.warning("Graph %s -> %s %s", what, r.status_code, code)
            raise GraphError(r.status_code, what)
        return r.json() if r.content else {}

    async def get_chat(self, chat_id: str) -> ChatInfo:
        d = await self._call("GET", f"/chats/{chat_id}?$expand=members", "get chat")
        members = [m for m in d.get("members", []) if m.get("userId")]
        return ChatInfo(
            id=d["id"], chat_type=d.get("chatType", ""), topic=d.get("topic") or "",
            member_aad_ids=[m["userId"] for m in members],
            member_names={m["userId"]: m.get("displayName") or "" for m in members},
        )

    async def _catalog_app_id(self) -> str:
        if not self._catalog_id:
            d = await self._call(
                "GET", f"/appCatalogs/teamsApps?$filter=externalId eq '{self._s.teams_app_manifest_id}'", "find app")
            if not d.get("value"):
                raise GraphError(404, "find app in org catalog")
            self._catalog_id = d["value"][0]["id"]
        return self._catalog_id

    async def install_app_in_chat(self, chat_id: str) -> None:
        app_id = await self._catalog_app_id()
        await self._call("POST", f"/chats/{chat_id}/installedApps", "install app in chat", json={
            "teamsApp@odata.bind": f"{GRAPH}/appCatalogs/teamsApps/{app_id}",
            "consentedPermissionSet": {"resourceSpecificPermissions": RSC_FOR_CHAT},
        })

    async def uninstall_app_from_chat(self, chat_id: str) -> None:
        app_id = await self._catalog_app_id()
        d = await self._call("GET", f"/chats/{chat_id}/installedApps?$expand=teamsApp", "list chat apps")
        for inst in d.get("value", []):
            if (inst.get("teamsApp") or {}).get("id") == app_id:
                await self._call("DELETE", f"/chats/{chat_id}/installedApps/{inst['id']}", "uninstall app")

    async def chat_messages(self, chat_id: str, top: int) -> list[GraphMessage]:
        d = await self._call("GET", f"/chats/{chat_id}/messages?$top={min(top, 50)}", "list chat messages")
        return _parse_messages(d.get("value", []))

    async def channel_messages(self, team_id: str, channel_id: str, top: int) -> list[GraphMessage]:
        d = await self._call(
            "GET", f"/teams/{team_id}/channels/{channel_id}/messages?$top={min(top, 50)}", "list channel messages")
        return _parse_messages(d.get("value", []))

    async def recent_group_chat_ids(self, user_aad_id: str, top: int = 20) -> list[str]:
        d = await self._call(
            "GET", f"/users/{user_aad_id}/chats?$filter=chatType eq 'group'&$top={min(top, 50)}", "list user chats")
        return [c["id"] for c in d.get("value", [])]

    async def ensure_chat_subscription(self, notification_url: str, client_state: str) -> str:
        """Create or renew the tenant-wide 'chat created' subscription (55 min, no lifecycle URL needed)."""
        expires = (datetime.now(timezone.utc) + timedelta(minutes=55)).isoformat().replace("+00:00", "Z")
        subs = (await self._call("GET", "/subscriptions", "list subscriptions")).get("value", [])
        mine = [x for x in subs if x.get("resource") == "/chats"]
        keep = next((x for x in mine if x.get("notificationUrl") == notification_url), None)
        for stale in mine:
            if stale is not keep:  # e.g. an old dev-tunnel URL
                await self._call("DELETE", f"/subscriptions/{stale['id']}", "delete stale subscription")
        if keep:
            await self._call("PATCH", f"/subscriptions/{keep['id']}", "renew subscription",
                             json={"expirationDateTime": expires})
            return keep["id"]
        d = await self._call("POST", "/subscriptions", "create subscription", json={
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": "/chats",
            "includeResourceData": False,
            "expirationDateTime": expires,
            "clientState": client_state,
        })
        return d["id"]


class MockGraph:
    """In-memory Graph for tests and offline demos. Register chats and messages, inspect calls."""

    def __init__(self) -> None:
        self.chats: dict[str, ChatInfo] = {}
        self.messages: dict[str, list[GraphMessage]] = {}
        self.installed: set[str] = set()
        self.uninstalled: set[str] = set()
        self.subscriptions: list[str] = []

    async def get_chat(self, chat_id: str) -> ChatInfo:
        if chat_id not in self.chats:
            raise GraphError(404, "get chat")
        return self.chats[chat_id]

    async def install_app_in_chat(self, chat_id: str) -> None:
        self.installed.add(chat_id)

    async def uninstall_app_from_chat(self, chat_id: str) -> None:
        self.installed.discard(chat_id)
        self.uninstalled.add(chat_id)

    async def chat_messages(self, chat_id: str, top: int) -> list[GraphMessage]:
        return self.messages.get(chat_id, [])[:top]

    async def channel_messages(self, team_id: str, channel_id: str, top: int) -> list[GraphMessage]:
        return self.messages.get(channel_id, [])[:top]

    async def recent_group_chat_ids(self, user_aad_id: str, top: int = 20) -> list[str]:
        return [c.id for c in self.chats.values() if c.chat_type == "group" and user_aad_id in c.member_aad_ids][:top]

    async def ensure_chat_subscription(self, notification_url: str, client_state: str) -> str:
        self.subscriptions.append(notification_url)
        return "mock-subscription"

    def reset(self) -> None:
        self.__init__()


@lru_cache
def get_graph_api() -> GraphService:
    s = get_settings()
    tenant, client_id, secret = s.graph_credentials
    if s.live_m365 and tenant and client_id and secret:
        return AzureGraph(s)
    if s.live_m365:
        log.warning("LIVE_M365=true but Graph credentials are missing; using mock Graph")
    return MockGraph()
