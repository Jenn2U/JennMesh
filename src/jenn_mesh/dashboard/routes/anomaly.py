"""Anomaly detection API routes — AI-powered telemetry analysis."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from jenn_mesh.core.anomaly_detector import AnomalyDetector

router = APIRouter(tags=["anomaly"])


def _get_detector(request: Request) -> AnomalyDetector:
    """Get or create an AnomalyDetector from request state."""
    detector = getattr(request.app.state, "anomaly_detector", None)
    if detector is not None:
        return detector
    # Fallback: create one without Ollama
    db = request.app.state.db
    return AnomalyDetector(db)


@router.get("/anomaly/status")
async def anomaly_status(request: Request) -> dict:
    """Get anomaly detector availability and configuration."""
    detector = _get_detector(request)
    return detector.get_status()


@router.get("/anomaly/history")
async def anomaly_history(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Get past anomaly detection alerts."""
    detector = _get_detector(request)
    alerts = detector.get_history(limit=limit)
    return {"count": len(alerts), "anomalies": alerts}


@router.get("/anomaly/fleet")
async def analyze_fleet(request: Request) -> dict:
    """Analyze all nodes with baseline deviations for anomalies.

    Uses Ollama for AI reasoning if available, otherwise baseline-only.
    """
    detector = _get_detector(request)
    reports = await detector.analyze_fleet()
    return {
        "analyzed": True,
        "anomaly_count": len(reports),
        "reports": reports,
    }


@router.get("/anomaly/{node_id}")
async def analyze_node(request: Request, node_id: str) -> dict:
    """Analyze a single node for anomalies.

    Returns anomaly report if deviation detected, otherwise empty result.
    """
    detector = _get_detector(request)
    report = await detector.analyze_node(node_id)

    if report is None:
        return {
            "node_id": node_id,
            "is_anomalous": False,
            "message": "No anomaly detected",
        }

    return report
