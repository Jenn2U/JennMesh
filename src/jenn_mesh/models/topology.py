"""Mesh topology models — network graph, edges, and connectivity analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from jenn_mesh.models.device import DeviceRole


class TopologyEdge(BaseModel):
    """A directed link between two mesh nodes.

    Directed because LoRa links are asymmetric — A hearing B at SNR 10
    doesn't mean B hears A at SNR 10 (terrain, antenna, power differences).
    """

    from_node: str = Field(description="Reporting node (who heard)")
    to_node: str = Field(description="Neighbor node (who was heard)")
    snr: Optional[float] = Field(default=None, description="Signal-to-noise ratio at from_node")
    rssi: Optional[int] = Field(default=None, description="Received signal strength indicator")
    last_updated: datetime = Field(
        default_factory=datetime.utcnow, description="When this edge was last reported"
    )


class TopologyNode(BaseModel):
    """Graph-enriched view of a device for topology context."""

    node_id: str = Field(description="Meshtastic node ID (e.g., '!28979058')")
    display_name: str = Field(default="", description="Human-readable name or node_id")
    role: DeviceRole = Field(default=DeviceRole.MOBILE, description="Device role")
    is_online: bool = Field(default=False, description="Within offline threshold")
    latitude: Optional[float] = Field(default=None, description="Last known latitude")
    longitude: Optional[float] = Field(default=None, description="Last known longitude")
    neighbor_count: int = Field(default=0, description="Number of edges (both directions)")
    edges: list[TopologyEdge] = Field(default_factory=list, description="All edges for this node")

    @property
    def is_isolated(self) -> bool:
        """True if this node has no edges — never appeared in any neighbor table."""
        return self.neighbor_count == 0


class TopologyGraph(BaseModel):
    """Full mesh topology snapshot with computed graph metrics."""

    nodes: list[TopologyNode] = Field(default_factory=list, description="All nodes in the mesh")
    edges: list[TopologyEdge] = Field(default_factory=list, description="All directed edges")
    total_nodes: int = Field(default=0, description="Number of nodes")
    total_edges: int = Field(default=0, description="Number of directed edges")
    connected_components: int = Field(
        default=0, description="Number of connected components (1 = fully connected)"
    )
    single_points_of_failure: list[str] = Field(
        default_factory=list,
        description="Node IDs whose removal would partition the graph",
    )
    last_updated: datetime = Field(
        default_factory=datetime.utcnow, description="When topology was last computed"
    )

    @property
    def is_fully_connected(self) -> bool:
        """True if the mesh forms a single connected component."""
        return self.connected_components == 1 and self.total_nodes > 0

    @property
    def has_spof(self) -> bool:
        """True if removing any single node would partition the network."""
        return len(self.single_points_of_failure) > 0
