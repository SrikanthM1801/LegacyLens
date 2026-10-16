"""GET /risks and GET /risks/{risk_id}: ranked knowledge risks with citations."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models import EntityType, RiskItem, RiskListResponse
from app.services import risk_store

router = APIRouter(tags=["risks"])


@router.get("/risks", response_model=RiskListResponse)
def list_risks(
    level: Optional[Literal["high", "medium", "low"]] = Query(None, description="Only risks of this level"),
    entity_type: Optional[EntityType] = Query(None, description="system | process | decision"),
    limit: Optional[int] = Query(None, ge=1, le=100, description="Keep only the top N after filtering"),
) -> RiskListResponse:
    cached = risk_store.get_risks()
    items = [
        r for r in cached.items
        if (level is None or r.risk_level == level) and (entity_type is None or r.entity_type == entity_type.value)
    ]
    return RiskListResponse(items=items[:limit] if limit else items, total=len(items), computed_at=cached.computed_at)


@router.get("/risks/{risk_id}", response_model=RiskItem)
def get_risk(risk_id: str) -> RiskItem:
    risk = risk_store.get_risk(risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"Unknown risk '{risk_id}'")
    return risk
