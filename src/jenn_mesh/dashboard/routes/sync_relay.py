"""Sync relay API routes — status, sessions, logs, and manual triggers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["sync-relay"])


class SyncTriggerRequest(BaseModel):
    """Body for manual sync trigger — requires explicit confirmation."""

    confirmed: bool = False


# ------------------------------------------------------------------
# Static paths FIRST (before any {id} / {node_id} path parameters)
# ------------------------------------------------------------------


@router.get("/sync-relay/status")
async def sync_relay_status(request: Request) -> dict:
    """Summary: active sessions, queue depth, last sync per node."""
    manager = getattr(request.app.state, "sync_relay_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Sync relay system unavailable")

    return manager.get_sync_status()


@router.get("/sync-relay/sessions")
async def list_sync_sessions(
    request: Request,
    node_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """List sync sessions, optionally filtered by node_id and status."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Queue entries (pending/sending)
    queue_entries = db.get_pending_sync_entries(node_id=node_id)

    # Log entries (all statuses including completed/failed)
    if node_id:
        log_entries = db.get_sync_log_for_node(node_id, limit=limit)
    else:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM crdt_sync_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            log_entries = [dict(r) for r in rows]

    # Merge queue + log for a comprehensive view
    sessions = []
    for entry in queue_entries:
        if status and entry.get("status") != status:
            continue
        sessions.append(entry)

    for entry in log_entries:
        if status and entry.get("status") != status:
            continue
        sessions.append(entry)

    sessions = sessions[:limit]
    return {"count": len(sessions), "sessions": sessions}


@router.get("/sync-relay/log")
async def sync_relay_log(
    request: Request,
    node_id: str | None = None,
    direction: str | None = None,
    limit: int = 50,
) -> dict:
    """Sync audit log, optionally filtered by node_id and direction."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if node_id:
        entries = db.get_sync_log_for_node(node_id, limit=limit)
    else:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM crdt_sync_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            entries = [dict(r) for r in rows]

    if direction:
        entries = [e for e in entries if e.get("direction") == direction]

    return {"count": len(entries), "entries": entries}


# ------------------------------------------------------------------
# Path-parameter routes
# ------------------------------------------------------------------


@router.get("/sync-relay/session/{session_id}")
async def get_sync_session(request: Request, session_id: str) -> dict:
    """Session detail with fragment status."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    fragments = db.get_fragments_for_session(session_id)
    if not fragments:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    total = fragments[0]["total"] if fragments else 0
    acked = sum(1 for f in fragments if f.get("status") == "acked")

    return {
        "session_id": session_id,
        "total_fragments": total,
        "acked_fragments": acked,
        "fragments": fragments,
    }


@router.post("/sync-relay/trigger/{node_id}")
async def trigger_sync(request: Request, node_id: str, body: SyncTriggerRequest) -> dict:
    """Manually trigger sync for a specific node.

    Requires ``confirmed: true`` — this consumes LoRa bandwidth and
    initiates a fragment exchange over the mesh radio.
    """
    manager = getattr(request.app.state, "sync_relay_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Sync relay system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Sync trigger requires explicit confirmation. Set confirmed=true.",
        )

    # Check that node exists
    db = getattr(request.app.state, "db", None)
    if db is not None:
        device = db.get_device(node_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Device '{node_id}' not found")

    try:
        result = manager.trigger_sync_for_node(node_id, remote_sv={})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sync trigger failed: {exc}")

    return result
