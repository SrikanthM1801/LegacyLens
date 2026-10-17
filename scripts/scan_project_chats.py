"""
Invite LegacyLens into project members' existing group chats (same rules as new chats).

Run from the repo root with the server running (the bot posts the consent cards):
    .venv/bin/python scripts/scan_project_chats.py

Needs LIVE_M365=true, the project roster (the bot records it when it is added to the team or on
`@LegacyLens resume` in the channel) and Chat.ReadBasic.All. Safe to re-run: chats already
asked are skipped. Prints counts only, never chat names or members.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bot.chat_watch import scan_existing_chats  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services.consent_store import get_consent_store, project_member_ids  # noqa: E402
from app.services.graph_api import get_graph_api  # noqa: E402


def main() -> None:
    if not get_settings().live_m365:
        print("FAIL  set LIVE_M365=true in .env first")
        sys.exit(1)
    store = get_consent_store()
    members = project_member_ids(store)
    if not members:
        print("FAIL  no project roster yet: type '@LegacyLens resume' in the project channel, then re-run")
        sys.exit(1)
    results = asyncio.run(scan_existing_chats(store, get_graph_api()))
    print(f"ok    {len(members)} project members, {len(results)} group chats checked:", dict(Counter(results.values())))


if __name__ == "__main__":
    main()
