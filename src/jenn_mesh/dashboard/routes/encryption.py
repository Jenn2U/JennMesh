"""Encryption audit API endpoints.

Provides fleet-wide and per-device encryption posture assessment.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["monitoring"])


def _get_auditor(request: Request):
    """Get or lazily create EncryptionAuditor."""
    auditor = getattr(request.app.state, "encryption_auditor", None)
    if auditor is not None:
        return auditor
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.encryption_auditor import EncryptionAuditor

    auditor = EncryptionAuditor(db=db)
    request.app.state.encryption_auditor = auditor
    return auditor


@router.get("/encryption/audit")
async def fleet_encryption_audit(request: Request) -> dict:
    """Full fleet encryption audit report.

    Returns per-device encryption status, weak channels, and a fleet-wide
    encryption score (0-100).
    """
    auditor = _get_auditor(request)
    report = auditor.audit_fleet()
    return report.model_dump()


@router.get("/encryption/score")
async def fleet_encryption_score(request: Request) -> dict:
    """Fleet encryption score (lightweight).

    Returns just the percentage of devices with strong encryption.
    """
    auditor = _get_auditor(request)
    score = auditor.get_fleet_encryption_score()
    return {"fleet_score": score}


@router.get("/encryption/audit/{node_id}")
async def device_encryption_audit(request: Request, node_id: str) -> dict:
    """Single device encryption audit.

    Returns encryption status and weak channel details for the specified
    node.
    """
    auditor = _get_auditor(request)
    result = auditor.audit_device(node_id)
    return result.model_dump()
