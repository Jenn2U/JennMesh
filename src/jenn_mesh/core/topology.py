"""Topology manager — mesh graph analysis and connectivity metrics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from jenn_mesh.core.registry import DeviceRegistry
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.topology import TopologyEdge, TopologyGraph, TopologyNode


class TopologyManager:
    """Mesh topology analysis backed by SQLite.

    Builds a graph from NEIGHBORINFO-reported directed edges, then computes
    connectivity metrics (connected components, single points of failure)
    using pure-Python graph algorithms — no networkx needed for ~100-node meshes.
    """

    def __init__(self, db: MeshDatabase, offline_threshold_seconds: int = 600):
        self._db = db
        self._registry = DeviceRegistry(db, offline_threshold_seconds)

    def update_neighbors(self, from_node: str, neighbors: list[dict]) -> None:
        """Process a NEIGHBORINFO packet — replace all outgoing edges for from_node.

        Args:
            from_node: The node that reported its neighbors.
            neighbors: List of dicts with keys 'node_id', and optionally 'snr', 'rssi'.
        """
        # Delete stale outgoing edges, then insert fresh ones
        self._db.delete_edges_for_node(from_node)
        for neighbor in neighbors:
            self._db.upsert_topology_edge(
                from_node=from_node,
                to_node=neighbor["node_id"],
                snr=neighbor.get("snr"),
                rssi=neighbor.get("rssi"),
            )

    def get_node_topology(self, node_id: str) -> Optional[TopologyNode]:
        """Get topology context for a single device."""
        device = self._registry.get_device(node_id)
        if device is None:
            return None

        edge_rows = self._db.get_edges_for_node(node_id)
        edges = [self._row_to_edge(r) for r in edge_rows]

        return TopologyNode(
            node_id=device.node_id,
            display_name=device.display_name,
            role=device.role,
            is_online=device.is_online,
            latitude=device.latitude,
            longitude=device.longitude,
            neighbor_count=len(edges),
            edges=edges,
        )

    def get_full_topology(self) -> TopologyGraph:
        """Build the complete mesh topology graph with computed metrics."""
        devices = self._registry.list_devices()
        all_edge_rows = self._db.get_all_edges()
        all_edges = [self._row_to_edge(r) for r in all_edge_rows]

        # Build edge lookup by node
        edges_by_node: dict[str, list[TopologyEdge]] = defaultdict(list)
        for edge in all_edges:
            edges_by_node[edge.from_node].append(edge)
            edges_by_node[edge.to_node].append(edge)

        nodes = []
        for device in devices:
            device_edges = edges_by_node.get(device.node_id, [])
            nodes.append(
                TopologyNode(
                    node_id=device.node_id,
                    display_name=device.display_name,
                    role=device.role,
                    is_online=device.is_online,
                    latitude=device.latitude,
                    longitude=device.longitude,
                    neighbor_count=len(device_edges),
                    edges=device_edges,
                )
            )

        # Build undirected adjacency for graph algorithms
        adj = self._build_undirected_adjacency(all_edges)
        node_ids = {d.node_id for d in devices}
        components = self._find_connected_components(node_ids, adj)
        spofs = self._find_articulation_points(node_ids, adj)

        return TopologyGraph(
            nodes=nodes,
            edges=all_edges,
            total_nodes=len(nodes),
            total_edges=len(all_edges),
            connected_components=len(components),
            single_points_of_failure=spofs,
        )

    def find_single_points_of_failure(self) -> list[str]:
        """Find articulation points — nodes whose removal partitions the graph."""
        all_edges = [self._row_to_edge(r) for r in self._db.get_all_edges()]
        adj = self._build_undirected_adjacency(all_edges)
        node_ids = {r["node_id"] for r in self._db.list_devices()}
        return self._find_articulation_points(node_ids, adj)

    def find_connected_components(self) -> list[list[str]]:
        """Find connected components in the undirected projection."""
        all_edges = [self._row_to_edge(r) for r in self._db.get_all_edges()]
        adj = self._build_undirected_adjacency(all_edges)
        node_ids = {r["node_id"] for r in self._db.list_devices()}
        return self._find_connected_components(node_ids, adj)

    def get_isolated_nodes(self) -> list[str]:
        """Get nodes with zero edges — never appeared in any neighbor table."""
        all_edges = self._db.get_all_edges()
        nodes_with_edges: set[str] = set()
        for edge in all_edges:
            nodes_with_edges.add(edge["from_node"])
            nodes_with_edges.add(edge["to_node"])

        all_nodes = {r["node_id"] for r in self._db.list_devices()}
        return sorted(all_nodes - nodes_with_edges)

    def prune_stale_edges(self, max_age_hours: int = 24) -> int:
        """Remove topology edges older than threshold."""
        return self._db.prune_stale_edges(max_age_hours)

    # --- Failover analysis ---

    def find_dependent_nodes(
        self, failed_node_id: str, edges: Optional[list[TopologyEdge]] = None
    ) -> list[str]:
        """Find nodes that would lose connectivity if *failed_node_id* goes offline.

        Removes the failed node from the undirected graph, finds connected
        components in the residual graph, identifies the "main" component
        (the largest one), and returns all nodes NOT in the main component.
        """
        if edges is None:
            edges = [self._row_to_edge(r) for r in self._db.get_all_edges()]
        adj = self._build_undirected_adjacency(edges)

        # Remove failed node from adjacency
        residual: dict[str, set[str]] = {}
        for node, neighbors in adj.items():
            if node == failed_node_id:
                continue
            residual[node] = neighbors - {failed_node_id}

        all_node_ids = {r["node_id"] for r in self._db.list_devices()}
        remaining = all_node_ids - {failed_node_id}

        components = self._find_connected_components(remaining, residual)
        if not components:
            return []

        # Main component = largest
        main = max(components, key=len)
        dependent: list[str] = []
        for comp in components:
            if comp is not main:
                dependent.extend(comp)
        return sorted(dependent)

    @staticmethod
    def find_alternative_paths(
        source: str,
        target: str,
        adj: dict[str, set[str]],
        exclude_node: str,
    ) -> bool:
        """BFS check: does a path exist from *source* to *target* when *exclude_node* is removed?"""
        if source == exclude_node or target == exclude_node:
            return False
        visited: set[str] = {exclude_node}
        queue = [source]
        while queue:
            current = queue.pop(0)
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            for neighbor in adj.get(current, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        return False

    def get_compensation_candidates(
        self,
        failed_node_id: str,
        edges: Optional[list[TopologyEdge]] = None,
        min_battery: int = 30,
    ) -> list[dict]:
        """Find nearby online nodes that could compensate for a failed relay.

        Returns neighbors of the failed node (and neighbors of dependent nodes)
        that are online and have battery >= *min_battery* (default 30%).
        Each result includes the node's current config-relevant fields.
        """
        if edges is None:
            edges = [self._row_to_edge(r) for r in self._db.get_all_edges()]
        adj = self._build_undirected_adjacency(edges)

        dependent_nodes = self.find_dependent_nodes(failed_node_id, edges)

        # Candidate pool: neighbors of failed node + neighbors of dependent nodes
        candidate_ids: set[str] = set()
        for node_id in [failed_node_id] + dependent_nodes:
            candidate_ids |= adj.get(node_id, set())
        # Exclude the failed node and dependent nodes themselves
        candidate_ids -= {failed_node_id}
        candidate_ids -= set(dependent_nodes)

        # Filter: online + sufficient battery
        devices = {r["node_id"]: r for r in self._db.list_devices()}
        candidates: list[dict] = []
        for cid in sorted(candidate_ids):
            dev = devices.get(cid)
            if dev is None:
                continue
            # Must be online (check is_online or recent last_seen)
            if dev.get("mesh_status") == "offline":
                continue
            battery = dev.get("battery_level")
            if battery is not None and battery < min_battery:
                continue
            candidates.append(
                {
                    "node_id": cid,
                    "long_name": dev.get("long_name", ""),
                    "role": dev.get("role", "CLIENT"),
                    "battery_level": battery,
                    "signal_snr": dev.get("signal_snr"),
                }
            )
        return candidates

    # --- Private helpers ---

    @staticmethod
    def _row_to_edge(row: dict) -> TopologyEdge:
        """Convert a database row to a TopologyEdge model."""
        last_updated = row.get("last_updated")
        if isinstance(last_updated, str):
            last_updated = datetime.fromisoformat(last_updated)
        elif last_updated is None:
            last_updated = datetime.utcnow()

        return TopologyEdge(
            from_node=row["from_node"],
            to_node=row["to_node"],
            snr=row.get("snr"),
            rssi=row.get("rssi"),
            last_updated=last_updated,
        )

    @staticmethod
    def _build_undirected_adjacency(
        edges: list[TopologyEdge],
    ) -> dict[str, set[str]]:
        """Build undirected adjacency list from directed edges.

        If A→B exists, adds both A-B and B-A to the undirected graph.
        Asymmetric links still provide connectivity in the undirected projection.
        """
        adj: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            adj[edge.from_node].add(edge.to_node)
            adj[edge.to_node].add(edge.from_node)
        return adj

    @staticmethod
    def _find_connected_components(node_ids: set[str], adj: dict[str, set[str]]) -> list[list[str]]:
        """DFS-based connected component discovery. O(V+E)."""
        visited: set[str] = set()
        components: list[list[str]] = []

        for node in sorted(node_ids):  # sorted for deterministic output
            if node not in visited:
                component: list[str] = []
                stack = [node]
                while stack:
                    current = stack.pop()
                    if current in visited:
                        continue
                    visited.add(current)
                    if current in node_ids:
                        component.append(current)
                    for neighbor in adj.get(current, set()):
                        if neighbor not in visited:
                            stack.append(neighbor)
                if component:
                    components.append(sorted(component))

        return components

    @staticmethod
    def _find_articulation_points(node_ids: set[str], adj: dict[str, set[str]]) -> list[str]:
        """Tarjan's algorithm for articulation point detection. O(V+E).

        A node is a single point of failure if removing it increases the
        number of connected components. Works on the undirected projection.
        """
        # Only consider nodes that are in the device registry
        graph_nodes = node_ids & set(adj.keys())
        if len(graph_nodes) <= 2:
            return []

        disc: dict[str, int] = {}
        low: dict[str, int] = {}
        parent: dict[str, Optional[str]] = {}
        ap: set[str] = set()
        timer = [0]

        def dfs(u: str) -> None:
            disc[u] = low[u] = timer[0]
            timer[0] += 1
            child_count = 0

            for v in adj.get(u, set()):
                if v not in graph_nodes:
                    continue
                if v not in disc:
                    child_count += 1
                    parent[v] = u
                    dfs(v)
                    low[u] = min(low[u], low[v])

                    # Root with 2+ DFS children
                    if parent.get(u) is None and child_count > 1:
                        ap.add(u)
                    # Non-root where child can't reach above u
                    if parent.get(u) is not None and low[v] >= disc[u]:
                        ap.add(u)
                elif v != parent.get(u):
                    low[u] = min(low[u], disc[v])

        for node in sorted(graph_nodes):
            if node not in disc:
                parent[node] = None
                dfs(node)

        return sorted(ap)
