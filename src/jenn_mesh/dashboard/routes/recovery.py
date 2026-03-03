"""Recovery command API routes — send, list, and track mesh recovery commands."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["recovery"])


class RecoveryCommandRequest(BaseModel):
    """Request body for sending a recovery command."""

    target_node_id: str = Field(description="Meshtastic node ID (e.g., '!a1b2c3d4')")
    command_type: str = Field(
        description="Command: reboot, restart_service, restart_ollama, system_status"
    )
    args: str = Field(
        default="", description="Command args (e.g., service name for restart_service)"
    )
    confirmed: bool = Field(
        default=False,
        description="Must be true — safety gate for remote command execution",
    )
    sender: str = Field(default="dashboard", description="Who is initiating the command")


@router.post("/recovery/send")
async def send_recovery_command(request: Request, body: RecoveryCommandRequest) -> dict:
    """Send a recovery command to an edge node via LoRa mesh.

    Requires `confirmed: true` in the request body — this executes OS-level
    commands on remote edge nodes that may have no internet connectivity.
    """
    manager = getattr(request.app.state, "recovery_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Recovery command system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Recovery commands require explicit confirmation. Set confirmed=true.",
        )

    try:
        cmd = manager.send_command(
            target_node_id=body.target_node_id,
            command_type=body.command_type,
            args=body.args,
            sender=body.sender,
            confirmed=body.confirmed,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        # Rate limiting
        raise HTTPException(status_code=429, detail=str(e))

    return {
        "command_id": cmd.id,
        "target_node_id": cmd.target_node_id,
        "command_type": cmd.command_type.value,
        "args": cmd.args,
        "status": cmd.status.value,
        "nonce": cmd.nonce,
        "sender": cmd.sender,
        "created_at": cmd.expires_at,
    }


@router.get("/recovery/commands")
async def list_recovery_commands(
    request: Request,
    target_node_id: str = Query(default=None, description="Filter by target node"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """List recovery command history, most recent first."""
    manager = getattr(request.app.state, "recovery_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Recovery command system unavailable")

    commands = manager.list_commands(target_node_id=target_node_id, limit=limit)
    return {
        "count": len(commands),
        "limit": limit,
        "commands": commands,
    }


@router.get("/recovery/command/{command_id}")
async def get_recovery_command(request: Request, command_id: int) -> dict:
    """Get a specific recovery command by ID."""
    manager = getattr(request.app.state, "recovery_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Recovery command system unavailable")

    command = manager.get_command(command_id)
    if command is None:
        raise HTTPException(status_code=404, detail="Recovery command not found")

    return command


@router.get("/recovery/status/{node_id}")
async def get_node_recovery_status(request: Request, node_id: str) -> dict:
    """Get recovery status summary for a specific node.

    Returns command history, pending command count, and recent activity.
    """
    manager = getattr(request.app.state, "recovery_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Recovery command system unavailable")

    return manager.get_node_recovery_status(node_id)
