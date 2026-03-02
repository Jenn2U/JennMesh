"""Config API routes — golden template CRUD, drift detection."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh.core.config_manager import ConfigManager

router = APIRouter(tags=["config"])


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
        return {"error": "Template not found", "role": role}
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
