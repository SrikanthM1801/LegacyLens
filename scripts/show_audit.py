"""
Print the audit trail (Azure Blob `audit` container) and the SharePoint file inventory.

Run from the repo root (uses your `az login`; no server needed):
    .venv/bin/python scripts/show_audit.py                     # last 24 h, newest first
    .venv/bin/python scripts/show_audit.py --hours 2 --action capture.file
    .venv/bin/python scripts/show_audit.py --files             # which SharePoint files are loaded
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
logging.disable(logging.WARNING)

from app.config import get_settings  # noqa: E402


def show_events(hours: float, action: str | None, limit: int) -> None:
    from app.services.audit import BlobAuditLog

    s = get_settings()
    now = datetime.now(timezone.utc)
    events = BlobAuditLog(s.azure_storage_account_url, s.audit_container).query(now - timedelta(hours=hours), now)
    if action:
        events = [e for e in events if e.action == action or e.action.startswith(action.rstrip(".") + ".")]
    events.sort(key=lambda e: e.timestamp, reverse=True)
    print(f"{len(events)} events in the last {hours:g} h:", dict(Counter(e.action for e in events)), "\n")
    for e in events[:limit]:
        what = e.details.get("file") or e.scope_name or e.scope_type or ""
        extra = e.details.get("change") or e.details.get("error") or e.details.get("reply") or ""
        who = e.actor_name or ""
        print(f"{e.timestamp:%Y-%m-%d %H:%M:%S}  {e.action:<28} {e.outcome:<8} {who[:22]:<22} {what[:45]:<45} {extra}")


def show_files() -> None:
    from app.services.source_registry import TableSourceRegistry

    reg = TableSourceRegistry(get_settings().azure_tables_account_url)
    files = sorted(reg.list(), key=lambda f: (f.path, f.name))
    print(f"{len(files)} SharePoint files:", dict(Counter(f.status for f in files)), "| last sync:", reg.last_sync(), "\n")
    for f in files:
        print(f"{f.status:<11} {f.chunks:>3} chunks  {(f.path + '/' + f.name).lstrip('/')[:60]:<60} {f.site_name}  {f.error or ''}")


def main() -> None:
    p = argparse.ArgumentParser(description="Show LegacyLens audit events or the SharePoint file inventory")
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--action", help="exact action or category, e.g. capture or capture.file")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--files", action="store_true", help="show loaded SharePoint files instead")
    a = p.parse_args()
    show_files() if a.files else show_events(a.hours, a.action, a.limit)


if __name__ == "__main__":
    main()
