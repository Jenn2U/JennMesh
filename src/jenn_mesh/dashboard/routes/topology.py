"""Topology API routes — mesh graph, connectivity, and SPOF analysis."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from jenn_mesh.core.topology import TopologyManager

router = APIRouter(tags=["topology"])


@router.get("/topology")
async def get_topology(request: Request) -> dict:
    """Get the full mesh topology graph with computed metrics."""
    db = request.app.state.db
    manager = TopologyManager(db)
    graph = manager.get_full_topology()

    return {
        "total_nodes": graph.total_nodes,
        "total_edges": graph.total_edges,
        "connected_components": graph.connected_components,
        "is_fully_connected": graph.is_fully_connected,
        "single_points_of_failure": graph.single_points_of_failure,
        "has_spof": graph.has_spof,
        "nodes": [
            {
                "node_id": n.node_id,
                "display_name": n.display_name,
                "role": n.role.value,
                "is_online": n.is_online,
                "latitude": n.latitude,
                "longitude": n.longitude,
                "neighbor_count": n.neighbor_count,
                "is_isolated": n.is_isolated,
            }
            for n in graph.nodes
        ],
        "edges": [
            {
                "from_node": e.from_node,
                "to_node": e.to_node,
                "snr": e.snr,
                "rssi": e.rssi,
                "last_updated": e.last_updated.isoformat(),
            }
            for e in graph.edges
        ],
    }


@router.get("/topology/spof")
async def single_points_of_failure(request: Request) -> dict:
    """Get nodes whose removal would partition the mesh."""
    db = request.app.state.db
    manager = TopologyManager(db)
    spofs = manager.find_single_points_of_failure()
    return {"count": len(spofs), "nodes": spofs}


@router.get("/topology/components")
async def connected_components(request: Request) -> dict:
    """Get connected components of the mesh graph."""
    db = request.app.state.db
    manager = TopologyManager(db)
    components = manager.find_connected_components()
    return {"count": len(components), "components": components}


@router.get("/topology/isolated")
async def isolated_nodes(request: Request) -> dict:
    """Get nodes with no topology edges — never appeared in any neighbor table."""
    db = request.app.state.db
    manager = TopologyManager(db)
    isolated = manager.get_isolated_nodes()
    return {"count": len(isolated), "nodes": isolated}


@router.get("/topology/{node_id}")
async def get_node_topology(request: Request, node_id: str) -> dict:
    """Get topology context for a specific device."""
    db = request.app.state.db
    manager = TopologyManager(db)
    node = manager.get_node_topology(node_id)

    if node is None:
        raise HTTPException(status_code=404, detail="Device not found")

    return {
        "node_id": node.node_id,
        "display_name": node.display_name,
        "role": node.role.value,
        "is_online": node.is_online,
        "latitude": node.latitude,
        "longitude": node.longitude,
        "neighbor_count": node.neighbor_count,
        "is_isolated": node.is_isolated,
        "edges": [
            {
                "from_node": e.from_node,
                "to_node": e.to_node,
                "snr": e.snr,
                "rssi": e.rssi,
                "last_updated": e.last_updated.isoformat(),
            }
            for e in node.edges
        ],
    }
