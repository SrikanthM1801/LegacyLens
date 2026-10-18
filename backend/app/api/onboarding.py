"""Onboarding assessments for the admin page: list sessions, read one in full."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.api.audit import require_admin
from app.models import Departure, OnboardingSession
from app.services.onboarding_store import get_onboarding_store

router = APIRouter(tags=["onboarding"])


@router.get("/onboarding/sessions", response_model=list[OnboardingSession])
def list_sessions(status: Optional[str] = None, x_admin_key: Optional[str] = Header(None)) -> list[OnboardingSession]:
    """Newest first. Includes the learning log, questions, answers and evaluation."""
    require_admin(x_admin_key)
    items = [s for s in get_onboarding_store().list() if status is None or s.status == status]
    return sorted(items, key=lambda s: s.assigned_at, reverse=True)


@router.get("/onboarding/sessions/{session_id}", response_model=OnboardingSession)
def get_session(session_id: str, x_admin_key: Optional[str] = Header(None)) -> OnboardingSession:
    require_admin(x_admin_key)
    s = get_onboarding_store().get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"Unknown onboarding session '{session_id}'")
    return s


@router.get("/departures", response_model=list[Departure])
def list_departures(x_admin_key: Optional[str] = Header(None)) -> list[Departure]:
    """Departure alerts: who, why (out-of-office / self / owner), topics at risk, risk before -> after, handover link."""
    require_admin(x_admin_key)
    from app.bot.departure import departures
    return sorted(departures().list(), key=lambda d: d.detected_at, reverse=True)
