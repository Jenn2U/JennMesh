"""Tests for topology data models."""

from datetime import datetime


from jenn_mesh.models.device import DeviceRole
from jenn_mesh.models.topology import TopologyEdge, TopologyGraph, TopologyNode


class TestTopologyEdge:
    def test_basic_creation(self):
        edge = TopologyEdge(from_node="!aaa", to_node="!bbb", snr=10.5, rssi=-85)
        assert edge.from_node == "!aaa"
        assert edge.to_node == "!bbb"
        assert edge.snr == 10.5
        assert edge.rssi == -85

    def test_optional_fields_default_none(self):
        edge = TopologyEdge(from_node="!aaa", to_node="!bbb")
        assert edge.snr is None
        assert edge.rssi is None

    def test_last_updated_defaults(self):
        edge = TopologyEdge(from_node="!aaa", to_node="!bbb")
        assert isinstance(edge.last_updated, datetime)


class TestTopologyNode:
    def test_basic_creation(self):
        node = TopologyNode(
            node_id="!aaa",
            display_name="Relay-HQ",
            role=DeviceRole.RELAY,
            is_online=True,
            neighbor_count=2,
        )
        assert node.node_id == "!aaa"
        assert node.role == DeviceRole.RELAY
        assert node.neighbor_count == 2

    def test_is_isolated_true(self):
        node = TopologyNode(node_id="!aaa", neighbor_count=0)
        assert node.is_isolated is True

    def test_is_isolated_false(self):
        node = TopologyNode(node_id="!aaa", neighbor_count=3)
        assert node.is_isolated is False

    def test_node_with_edges(self):
        edges = [
            TopologyEdge(from_node="!aaa", to_node="!bbb", snr=10.5),
            TopologyEdge(from_node="!ccc", to_node="!aaa", snr=8.0),
        ]
        node = TopologyNode(
            node_id="!aaa",
            neighbor_count=2,
            edges=edges,
        )
        assert len(node.edges) == 2


class TestTopologyGraph:
    def test_empty_graph(self):
        graph = TopologyGraph()
        assert graph.total_nodes == 0
        assert graph.total_edges == 0
        assert graph.connected_components == 0
        assert graph.is_fully_connected is False
        assert graph.has_spof is False

    def test_fully_connected(self):
        graph = TopologyGraph(
            total_nodes=3,
            total_edges=4,
            connected_components=1,
        )
        assert graph.is_fully_connected is True

    def test_not_fully_connected(self):
        graph = TopologyGraph(
            total_nodes=4,
            total_edges=2,
            connected_components=2,
        )
        assert graph.is_fully_connected is False

    def test_has_spof(self):
        graph = TopologyGraph(
            total_nodes=3,
            single_points_of_failure=["!bbb"],
        )
        assert graph.has_spof is True

    def test_no_spof(self):
        graph = TopologyGraph(total_nodes=3, single_points_of_failure=[])
        assert graph.has_spof is False
