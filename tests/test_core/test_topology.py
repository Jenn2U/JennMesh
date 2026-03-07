"""Tests for TopologyManager — graph analysis, SPOF detection, connectivity."""

from jenn_mesh.core.topology import TopologyManager
from jenn_mesh.db import MeshDatabase


class TestUpdateNeighbors:
    def test_insert_neighbors(self, db: MeshDatabase):
        db.upsert_device("!aaa")
        db.upsert_device("!bbb")
        db.upsert_device("!ccc")

        manager = TopologyManager(db)
        manager.update_neighbors(
            "!aaa",
            [
                {"node_id": "!bbb", "snr": 10.5, "rssi": -85},
                {"node_id": "!ccc", "snr": 5.0},
            ],
        )
        edges = db.get_all_edges()
        assert len(edges) == 2
        from_nodes = {e["from_node"] for e in edges}
        assert from_nodes == {"!aaa"}

    def test_replace_neighbors(self, db: MeshDatabase):
        """update_neighbors replaces old outgoing edges."""
        db.upsert_device("!aaa")
        db.upsert_device("!bbb")
        db.upsert_device("!ccc")

        manager = TopologyManager(db)
        manager.update_neighbors("!aaa", [{"node_id": "!bbb"}])
        assert len(db.get_all_edges()) == 1

        manager.update_neighbors("!aaa", [{"node_id": "!ccc"}])
        edges = db.get_all_edges()
        assert len(edges) == 1
        assert edges[0]["to_node"] == "!ccc"

    def test_empty_neighbors_clears_edges(self, db: MeshDatabase):
        db.upsert_device("!aaa")
        db.upsert_device("!bbb")

        manager = TopologyManager(db)
        manager.update_neighbors("!aaa", [{"node_id": "!bbb"}])
        assert len(db.get_all_edges()) == 1

        manager.update_neighbors("!aaa", [])
        assert len(db.get_all_edges()) == 0


class TestGetNodeTopology:
    def test_node_with_edges(self, populated_db: MeshDatabase):
        manager = TopologyManager(populated_db)
        node = manager.get_node_topology("!bbb22222")

        assert node is not None
        assert node.node_id == "!bbb22222"
        assert node.display_name == "Gateway-Edge1"
        assert node.neighbor_count == 3  # 2 outgoing + 1 incoming (from relay)
        assert node.is_isolated is False

    def test_isolated_node(self, populated_db: MeshDatabase):
        manager = TopologyManager(populated_db)
        node = manager.get_node_topology("!ddd44444")

        assert node is not None
        assert node.neighbor_count == 0
        assert node.is_isolated is True

    def test_nonexistent_node_returns_none(self, populated_db: MeshDatabase):
        manager = TopologyManager(populated_db)
        assert manager.get_node_topology("!zzz99999") is None


class TestGetFullTopology:
    def test_graph_structure(self, populated_db: MeshDatabase):
        manager = TopologyManager(populated_db)
        graph = manager.get_full_topology()

        assert graph.total_nodes == 4
        assert graph.total_edges == 3  # 3 directed edges in conftest

    def test_graph_connectivity(self, populated_db: MeshDatabase):
        """Test fleet: relay↔gateway→mobile, sensor isolated → 2 components."""
        manager = TopologyManager(populated_db)
        graph = manager.get_full_topology()

        # relay, gateway, mobile connected; sensor isolated
        assert graph.connected_components == 2
        assert graph.is_fully_connected is False

    def test_empty_graph(self, db: MeshDatabase):
        manager = TopologyManager(db)
        graph = manager.get_full_topology()
        assert graph.total_nodes == 0
        assert graph.total_edges == 0
        assert graph.connected_components == 0


class TestSinglePointsOfFailure:
    def test_linear_chain(self, db: MeshDatabase):
        """A-B-C: B is a SPOF (removing B splits A from C)."""
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)

        manager = TopologyManager(db)
        spofs = manager.find_single_points_of_failure()
        assert "!b" in spofs

    def test_ring_no_spof(self, db: MeshDatabase):
        """A-B-C-A ring: no SPOFs (every node has alternate path)."""
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)
        db.upsert_topology_edge("!c", "!a", snr=10.0)

        manager = TopologyManager(db)
        spofs = manager.find_single_points_of_failure()
        assert spofs == []

    def test_star_hub_is_spof(self, db: MeshDatabase):
        """Star: hub connects to A, B, C — hub is the SPOF."""
        for n in ["!hub", "!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!hub", "!a", snr=10.0)
        db.upsert_topology_edge("!hub", "!b", snr=10.0)
        db.upsert_topology_edge("!hub", "!c", snr=10.0)

        manager = TopologyManager(db)
        spofs = manager.find_single_points_of_failure()
        assert "!hub" in spofs
        assert len(spofs) == 1

    def test_populated_fleet_spof(self, populated_db: MeshDatabase):
        """Test fleet: gateway is SPOF (connects relay to mobile)."""
        manager = TopologyManager(populated_db)
        spofs = manager.find_single_points_of_failure()
        assert "!bbb22222" in spofs


class TestConnectedComponents:
    def test_single_component(self, db: MeshDatabase):
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)

        manager = TopologyManager(db)
        components = manager.find_connected_components()
        assert len(components) == 1
        assert sorted(components[0]) == ["!a", "!b", "!c"]

    def test_two_components(self, db: MeshDatabase):
        for n in ["!a", "!b", "!c", "!d"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!c", "!d", snr=10.0)

        manager = TopologyManager(db)
        components = manager.find_connected_components()
        assert len(components) == 2

    def test_isolated_nodes_are_own_components(self, db: MeshDatabase):
        for n in ["!a", "!b", "!isolated"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)

        manager = TopologyManager(db)
        components = manager.find_connected_components()
        assert len(components) == 2  # {a, b} and {isolated}


class TestIsolatedNodes:
    def test_finds_isolated(self, populated_db: MeshDatabase):
        manager = TopologyManager(populated_db)
        isolated = manager.get_isolated_nodes()
        assert "!ddd44444" in isolated

    def test_connected_nodes_not_isolated(self, populated_db: MeshDatabase):
        manager = TopologyManager(populated_db)
        isolated = manager.get_isolated_nodes()
        assert "!aaa11111" not in isolated
        assert "!bbb22222" not in isolated


class TestPruneStaleEdges:
    def test_prune_removes_old_edges(self, db: MeshDatabase):
        db.upsert_device("!a")
        db.upsert_device("!b")
        db.upsert_topology_edge("!a", "!b", snr=10.0)

        # Manually age the edge
        with db.connection() as conn:
            conn.execute(
                """UPDATE topology_edges SET last_updated = datetime('now', '-48 hours')
                   WHERE from_node = '!a'"""
            )

        manager = TopologyManager(db)
        deleted = manager.prune_stale_edges(max_age_hours=24)
        assert deleted == 1
        assert len(db.get_all_edges()) == 0

    def test_prune_keeps_fresh_edges(self, db: MeshDatabase):
        db.upsert_device("!a")
        db.upsert_device("!b")
        db.upsert_topology_edge("!a", "!b", snr=10.0)

        manager = TopologyManager(db)
        deleted = manager.prune_stale_edges(max_age_hours=24)
        assert deleted == 0
        assert len(db.get_all_edges()) == 1


class TestDBTopologyEdgeMethods:
    """Test the raw database methods for topology edges."""

    def test_upsert_and_get_all(self, db: MeshDatabase):
        db.upsert_device("!a")
        db.upsert_device("!b")
        db.upsert_topology_edge("!a", "!b", snr=10.0, rssi=-85)

        edges = db.get_all_edges()
        assert len(edges) == 1
        assert edges[0]["snr"] == 10.0
        assert edges[0]["rssi"] == -85

    def test_upsert_updates_existing(self, db: MeshDatabase):
        db.upsert_device("!a")
        db.upsert_device("!b")
        db.upsert_topology_edge("!a", "!b", snr=5.0)
        db.upsert_topology_edge("!a", "!b", snr=12.0)

        edges = db.get_all_edges()
        assert len(edges) == 1
        assert edges[0]["snr"] == 12.0

    def test_get_edges_for_node(self, db: MeshDatabase):
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!c", "!a", snr=5.0)

        edges = db.get_edges_for_node("!a")
        assert len(edges) == 2  # a→b and c→a

    def test_delete_edges_for_node(self, db: MeshDatabase):
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!a", "!c", snr=5.0)
        db.upsert_topology_edge("!c", "!a", snr=8.0)  # incoming, should NOT be deleted

        deleted = db.delete_edges_for_node("!a")
        assert deleted == 2
        edges = db.get_all_edges()
        assert len(edges) == 1
        assert edges[0]["from_node"] == "!c"

    def test_schema_version_is_current(self, db: MeshDatabase):
        with db.connection() as conn:
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
        from jenn_mesh.db import SCHEMA_VERSION

        assert row["version"] == SCHEMA_VERSION

    def test_topology_edges_table_exists(self, db: MeshDatabase):
        with db.connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='topology_edges'"
            ).fetchall()
        assert len(tables) == 1


# ── Failover analysis methods ────────────────────────────────────────


class TestFindDependentNodes:
    """Tests for find_dependent_nodes() — partition detection after node removal."""

    def test_linear_chain_middle_removed(self, db: MeshDatabase):
        """A-B-C: removing B isolates either A or C (one becomes dependent)."""
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)

        manager = TopologyManager(db)
        dependent = manager.find_dependent_nodes("!b")
        # A and C are in separate components after B removed;
        # the smaller component is "dependent"
        assert len(dependent) == 1
        assert dependent[0] in ("!a", "!c")

    def test_star_hub_removed(self, db: MeshDatabase):
        """Star: hub connects A, B, C. Removing hub → all 3 are isolated components."""
        for n in ["!hub", "!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!hub", "!a", snr=10.0)
        db.upsert_topology_edge("!hub", "!b", snr=10.0)
        db.upsert_topology_edge("!hub", "!c", snr=10.0)

        manager = TopologyManager(db)
        dependent = manager.find_dependent_nodes("!hub")
        # All 3 leaf nodes become singletons; the "main" component is one of them,
        # so 2 of the 3 are dependent
        assert len(dependent) == 2

    def test_ring_no_dependents(self, db: MeshDatabase):
        """A-B-C-A ring: removing any node leaves the other two connected."""
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)
        db.upsert_topology_edge("!c", "!a", snr=10.0)

        manager = TopologyManager(db)
        dependent = manager.find_dependent_nodes("!b")
        assert dependent == []

    def test_leaf_node_removed(self, db: MeshDatabase):
        """Removing a leaf has no dependents (rest stays connected)."""
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)

        manager = TopologyManager(db)
        dependent = manager.find_dependent_nodes("!a")
        assert dependent == []

    def test_populated_fleet_gateway_removed(self, populated_db: MeshDatabase):
        """Test fleet: gateway (!bbb22222) connects relay to mobile. Removing it
        isolates mobile from relay → mobile is dependent."""
        manager = TopologyManager(populated_db)
        dependent = manager.find_dependent_nodes("!bbb22222")
        # Mobile (!ccc33333) should be dependent (cut off from relay)
        assert "!ccc33333" in dependent


class TestFindAlternativePaths:
    """Tests for find_alternative_paths() — BFS path check with node exclusion."""

    def test_path_exists_without_excluded(self, db: MeshDatabase):
        """A-B-C + A-C: excluding B, path A→C still exists."""
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)
        db.upsert_topology_edge("!a", "!c", snr=10.0)

        adj: dict[str, set[str]] = {"!a": {"!b", "!c"}, "!b": {"!a", "!c"}, "!c": {"!a", "!b"}}
        assert TopologyManager.find_alternative_paths("!a", "!c", adj, "!b") is True

    def test_no_path_without_excluded(self, db: MeshDatabase):
        """A-B-C linear: excluding B, no path A→C."""
        adj: dict[str, set[str]] = {"!a": {"!b"}, "!b": {"!a", "!c"}, "!c": {"!b"}}
        assert TopologyManager.find_alternative_paths("!a", "!c", adj, "!b") is False

    def test_source_is_excluded(self):
        """If source == exclude_node, always returns False."""
        adj: dict[str, set[str]] = {"!a": {"!b"}, "!b": {"!a"}}
        assert TopologyManager.find_alternative_paths("!a", "!b", adj, "!a") is False

    def test_target_is_excluded(self):
        """If target == exclude_node, always returns False."""
        adj: dict[str, set[str]] = {"!a": {"!b"}, "!b": {"!a"}}
        assert TopologyManager.find_alternative_paths("!a", "!b", adj, "!b") is False


class TestGetCompensationCandidates:
    """Tests for get_compensation_candidates() — nearby healthy compensators."""

    def test_returns_neighbors_of_failed_node(self, db: MeshDatabase):
        """Chain A-B-C-D: failing B, A stays in main component, C is dependent.
        D is neighbor of C (dependent) so D could be a candidate if connected."""
        for n in ["!a", "!b", "!c", "!d"]:
            db.upsert_device(n, battery_level=80)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)
        # Add a cross-link so !d stays reachable from !a when !b is removed
        db.upsert_topology_edge("!a", "!d", snr=10.0)
        db.upsert_topology_edge("!d", "!c", snr=5.0)

        manager = TopologyManager(db)
        candidates = manager.get_compensation_candidates("!b")
        candidate_ids = {c["node_id"] for c in candidates}
        # !a is neighbor of !b, in main component → candidate
        assert "!a" in candidate_ids
        assert "!b" not in candidate_ids

    def test_excludes_low_battery_nodes(self, db: MeshDatabase):
        """Nodes with battery < 30% are excluded from candidates."""
        # Topology: anchor-hub-low_bat, anchor-hub-ok_bat, anchor-ok_bat (cross-link)
        # When hub removed: anchor, ok_bat remain connected (cross-link); low_bat isolated
        db.upsert_device("!anchor", battery_level=90)
        db.upsert_device("!hub", battery_level=80)
        db.upsert_device("!low_bat", battery_level=15)
        db.upsert_device("!ok_bat", battery_level=50)
        db.upsert_topology_edge("!anchor", "!hub", snr=10.0)
        db.upsert_topology_edge("!hub", "!low_bat", snr=10.0)
        db.upsert_topology_edge("!hub", "!ok_bat", snr=10.0)
        db.upsert_topology_edge("!anchor", "!ok_bat", snr=10.0)  # cross-link

        manager = TopologyManager(db)
        candidates = manager.get_compensation_candidates("!hub")
        candidate_ids = {c["node_id"] for c in candidates}
        # !ok_bat is neighbor of !hub and stays in main component → candidate
        assert "!ok_bat" in candidate_ids
        # !low_bat is isolated (dependent) after hub removal → excluded anyway
        # but also, if it were in main component, battery < 30% would exclude it
        assert "!low_bat" not in candidate_ids

    def test_excludes_dependent_nodes(self, db: MeshDatabase):
        """Dependent nodes (those cut off) should not be candidates."""
        for n in ["!a", "!b", "!c"]:
            db.upsert_device(n, battery_level=80)
        db.upsert_topology_edge("!a", "!b", snr=10.0)
        db.upsert_topology_edge("!b", "!c", snr=10.0)

        manager = TopologyManager(db)
        # Removing !b: !c is dependent on !b
        candidates = manager.get_compensation_candidates("!b")
        candidate_ids = {c["node_id"] for c in candidates}
        # !a is a neighbor of !b but not dependent, so it's a candidate
        # !c is dependent so should not be a candidate
        assert "!c" not in candidate_ids

    def test_empty_when_no_neighbors(self, db: MeshDatabase):
        """Isolated node has no compensation candidates."""
        db.upsert_device("!isolated", battery_level=80)
        manager = TopologyManager(db)
        candidates = manager.get_compensation_candidates("!isolated")
        assert candidates == []
