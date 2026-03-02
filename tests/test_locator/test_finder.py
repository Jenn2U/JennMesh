"""Tests for lost node locator."""

from jenn_mesh.db import MeshDatabase
from jenn_mesh.locator.finder import LostNodeFinder
from jenn_mesh.models.location import LostNodeQuery


class TestLostNodeFinder:
    def test_locate_known_node(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        result = finder.locate(LostNodeQuery(target_node_id="!aaa11111"))
        assert result.is_found is True
        assert result.last_known_position is not None
        assert result.last_known_position.latitude == 30.2672

    def test_locate_unknown_node(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        result = finder.locate(LostNodeQuery(target_node_id="!zzz99999"))
        assert result.is_found is False
        assert result.confidence == "unknown"

    def test_confidence_high_for_recent(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        result = finder.locate(LostNodeQuery(target_node_id="!aaa11111"))
        # Position was just added → should be "high" confidence
        assert result.confidence == "high"

    def test_nearby_nodes_found(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        result = finder.locate(LostNodeQuery(target_node_id="!aaa11111", search_radius_meters=5000))
        # Gateway is ~400m away, should be in nearby
        nearby_ids = {n.node_id for n in result.nearby_nodes}
        assert "!bbb22222" in nearby_ids

    def test_nearby_excludes_self(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        result = finder.locate(LostNodeQuery(target_node_id="!aaa11111", search_radius_meters=5000))
        nearby_ids = {n.node_id for n in result.nearby_nodes}
        assert "!aaa11111" not in nearby_ids


class TestEdgeNodeResolution:
    def test_resolve_radio_id_passthrough(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        # IDs starting with ! are radio node IDs — pass through unchanged
        assert finder._resolve_to_radio_id("!aaa11111") == "!aaa11111"

    def test_resolve_edge_device_to_radio(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        # bbb22222 is associated with "edge-node-pi4-01"
        radio_id = finder._resolve_to_radio_id("edge-node-pi4-01")
        assert radio_id == "!bbb22222"

    def test_resolve_unknown_edge_device(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        # Unknown edge device falls back to using the ID directly
        assert finder._resolve_to_radio_id("unknown-device") == "unknown-device"

    def test_locate_edge_node_convenience(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        result = finder.locate_edge_node("edge-node-pi4-01")
        assert result.is_found is True
        assert result.target_node_id == "edge-node-pi4-01"


class TestConfidenceComputation:
    def test_unknown_no_position(self, populated_db: MeshDatabase):
        finder = LostNodeFinder(populated_db)
        assert finder._compute_confidence(None, None, 72) == "unknown"

    def test_low_no_age(self, populated_db: MeshDatabase):
        from jenn_mesh.models.location import GPSPosition

        finder = LostNodeFinder(populated_db)
        pos = GPSPosition(node_id="!x", latitude=0.0, longitude=0.0)
        assert finder._compute_confidence(pos, None, 72) == "low"

    def test_high_within_one_hour(self, populated_db: MeshDatabase):
        from jenn_mesh.models.location import GPSPosition

        finder = LostNodeFinder(populated_db)
        pos = GPSPosition(node_id="!x", latitude=0.0, longitude=0.0)
        assert finder._compute_confidence(pos, 0.5, 72) == "high"

    def test_medium_within_24_hours(self, populated_db: MeshDatabase):
        from jenn_mesh.models.location import GPSPosition

        finder = LostNodeFinder(populated_db)
        pos = GPSPosition(node_id="!x", latitude=0.0, longitude=0.0)
        assert finder._compute_confidence(pos, 12.0, 72) == "medium"

    def test_stale_beyond_max_age(self, populated_db: MeshDatabase):
        from jenn_mesh.models.location import GPSPosition

        finder = LostNodeFinder(populated_db)
        pos = GPSPosition(node_id="!x", latitude=0.0, longitude=0.0)
        assert finder._compute_confidence(pos, 100.0, 72) == "stale"
