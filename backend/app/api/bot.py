"""POST /api/messages: the Teams bot endpoint. Azure Bot Service delivers every Teams activity here."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.bot.agent import get_bot

router = APIRouter(tags=["bot"])


@router.post("/api/messages")
async def messages(request: Request) -> Response:
    return await get_bot().process(request)


@router.get("/api/messages")
def messages_probe() -> dict:
    """Lets you check the tunnel reaches the bot route from a browser."""
    return {"status": "ok"}
