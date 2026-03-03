"""Config API routes — golden template CRUD, drift detection, remediation."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jenn_mesh.core.config_manager import ConfigManager

router = APIRouter(tags=["config"])


class RemediateRequest(BaseModel):
    """Request body for drift remediation endpoints."""

    confirmed: bool = Field(
        default=False,
        description="Must be true — safety gate for pushing config to remote devices",
    )
    operator: str = Field(default="dashboard", description="Who initiated the remediation")


@router.get("/config/templates")
async def list_templates(request: Request) -> dict:
    """List all golden config templates."""
    db = request.app.state.db
    cm = ConfigManager(db)
    templates = cm.load_templates_from_disk()
    return {
        "count": len(templates),
        "templates": [
            {"role": role, "hash": cm.get_template_hash(role) or ""} for role in templates
        ],
    }


@router.get("/config/templates/{role}")
async def get_template(request: Request, role: str) -> dict:
    """Get a golden config template by role."""
    db = request.app.state.db
    cm = ConfigManager(db)
    content = cm.get_template(role)
    if content is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {
        "role": role,
        "yaml_content": content,
        "hash": cm.get_template_hash(role),
    }


@router.get("/config/drift")
async def drift_report(request: Request) -> dict:
    """Get configuration drift report for all devices."""
    db = request.app.state.db
    cm = ConfigManager(db)
    drifted = cm.get_drift_report()
    return {"count": len(drifted), "drifted_devices": drifted}


# ── Drift remediation endpoints ──────────────────────────────────────
# IMPORTANT: remediate-all (static) MUST be before {node_id} (param)
# to prevent FastAPI from capturing "remediate-all" as a node_id.


@router.post("/config/drift/remediate-all")
async def remediate_all(request: Request, body: RemediateRequest) -> dict:
    """Remediate all drifted devices by pushing golden templates.

    Requires `confirmed: true` — this pushes config to remote devices over mesh.
    """
    manager = getattr(request.app.state, "drift_remediation_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Drift remediation system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Remediation requires explicit confirmation. Set confirmed=true.",
        )

    result = manager.remediate_all(operator=body.operator)
    return result


@router.get("/config/drift/{node_id}/preview")
async def preview_remediation(request: Request, node_id: str) -> dict:
    """Preview remediation for a drifted device.

    Shows the golden template YAML that would be pushed, along with
    hash comparison data so the operator can verify before confirming.
    """
    manager = getattr(request.app.state, "drift_remediation_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Drift remediation system unavailable")

    try:
        return manager.preview_remediation(node_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/config/drift/{node_id}/remediate")
async def remediate_device(request: Request, node_id: str, body: RemediateRequest) -> dict:
    """Push golden template to a specific drifted device.

    Requires `confirmed: true` — this executes a remote config push via mesh.
    On failure, the push is automatically enqueued for retry with exponential backoff.
    """
    manager = getattr(request.app.state, "drift_remediation_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Drift remediation system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Remediation requires explicit confirmation. Set confirmed=true.",
        )

    try:
        return manager.remediate_device(node_id, operator=body.operator)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/config/drift/{node_id}/status")
async def remediation_status(request: Request, node_id: str) -> dict:
    """Get remediation status for a device.

    Returns drift state, pending queue entries, active alerts,
    and recent remediation log entries.
    """
    manager = getattr(request.app.state, "drift_remediation_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Drift remediation system unavailable")

    try:
        return manager.get_remediation_status(node_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
