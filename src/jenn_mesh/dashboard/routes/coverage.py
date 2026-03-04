"""Coverage mapping API routes — heatmap, dead zones, stats, export."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from jenn_mesh.core.coverage_mapper import CoverageMapper

router = APIRouter(tags=["coverage"])


def _get_mapper(request: Request) -> CoverageMapper:
    """Get or create a CoverageMapper from request state."""
    mapper = getattr(request.app.state, "coverage_mapper", None)
    if mapper is not None:
        return mapper
    db = request.app.state.db
    return CoverageMapper(db)


@router.get("/coverage/heatmap")
async def coverage_heatmap(
    request: Request,
    min_lat: float = Query(-90.0),
    max_lat: float = Query(90.0),
    min_lon: float = Query(-180.0),
    max_lon: float = Query(180.0),
    resolution: float = Query(100.0, ge=10, le=10000),
) -> dict:
    """Generate coverage heatmap grid for a bounding box.

    Returns grid cells with aggregated RSSI data for map rendering.
    """
    mapper = _get_mapper(request)
    heatmap = mapper.get_heatmap(
        bounds=(min_lat, max_lat, min_lon, max_lon),
        resolution=resolution,
    )
    return {
        "bounds": {
            "min_lat": heatmap.min_lat,
            "max_lat": heatmap.max_lat,
            "min_lon": heatmap.min_lon,
            "max_lon": heatmap.max_lon,
        },
        "resolution_m": heatmap.resolution_m,
        "total_samples": heatmap.total_samples,
        "cell_count": len(heatmap.cells),
        "cells": [
            {
                "lat": c.lat_center,
                "lon": c.lon_center,
                "avg_rssi": round(c.avg_rssi, 1),
                "min_rssi": c.min_rssi,
                "max_rssi": c.max_rssi,
                "sample_count": c.sample_count,
            }
            for c in heatmap.cells
        ],
    }


@router.get("/coverage/dead-zones")
async def coverage_dead_zones(
    request: Request,
    min_rssi: float = Query(-110.0),
) -> dict:
    """Identify areas with poor or no coverage."""
    mapper = _get_mapper(request)
    zones = mapper.get_dead_zones(min_rssi=min_rssi)
    return {"count": len(zones), "dead_zones": zones}


@router.get("/coverage/stats")
async def coverage_stats(request: Request) -> dict:
    """Fleet-wide coverage summary statistics."""
    mapper = _get_mapper(request)
    stats = mapper.get_coverage_stats()
    return {
        "total_samples": stats.total_samples,
        "avg_rssi": stats.avg_rssi,
        "min_rssi": stats.min_rssi,
        "max_rssi": stats.max_rssi,
        "dead_zone_count": stats.dead_zone_count,
        "last_sample_at": stats.last_sample_at,
    }


@router.get("/coverage/export")
async def coverage_export(
    request: Request,
    min_lat: float = Query(-90.0),
    max_lat: float = Query(90.0),
    min_lon: float = Query(-180.0),
    max_lon: float = Query(180.0),
) -> dict:
    """Export coverage as GeoJSON for external GIS tools."""
    mapper = _get_mapper(request)
    return mapper.export_geojson(bounds=(min_lat, max_lat, min_lon, max_lon))
