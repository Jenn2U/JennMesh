"""Tests for coverage mapper — spatial heatmap grid generation."""

from __future__ import annotations

from jenn_mesh.core.coverage_mapper import CoverageMapper
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.coverage import CoverageGridCell, CoverageHeatmap, CoverageStats

# ── Helpers ─────────────────────────────────────────────────────────


def _seed_coverage(db: MeshDatabase, count: int = 10):
    """Seed coverage samples around Austin, TX."""
    base_lat = 30.2672
    base_lon = -97.7431
    for i in range(count):
        db.add_coverage_sample(
            from_node="!aaa11111",
            to_node="!bbb22222",
            latitude=base_lat + (i * 0.001),
            longitude=base_lon + (i * 0.001),
            rssi=-80.0 - i,
            snr=10.0 - (i * 0.5),
        )


def _seed_dead_zone(db: MeshDatabase):
    """Seed samples with very poor signal (dead zone)."""
    for i in range(5):
        db.add_coverage_sample(
            from_node="!aaa11111",
            to_node="!bbb22222",
            latitude=31.0 + (i * 0.0001),
            longitude=-98.0 + (i * 0.0001),
            rssi=-115.0 - i,
        )


# ── Init ────────────────────────────────────────────────────────────


class TestCoverageMapperInit:
    def test_default_resolution(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db)
        assert mapper._grid_res == 100.0

    def test_custom_resolution(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db, grid_resolution_m=50.0)
        assert mapper._grid_res == 50.0


# ── Recording ───────────────────────────────────────────────────────


class TestRecordObservation:
    def test_record_sample(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db)
        sample_id = mapper.record_observation(
            from_node="!aaa11111",
            to_node="!bbb22222",
            lat=30.2672,
            lon=-97.7431,
            rssi=-85.0,
            snr=10.5,
        )
        assert sample_id > 0

    def test_record_without_snr(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db)
        sample_id = mapper.record_observation(
            from_node="!aaa11111",
            to_node="!bbb22222",
            lat=30.0,
            lon=-97.0,
            rssi=-90.0,
        )
        assert sample_id > 0


# ── Heatmap Generation ──────────────────────────────────────────────


class TestHeatmap:
    def test_empty_heatmap(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db)
        heatmap = mapper.get_heatmap((30.0, 31.0, -98.0, -97.0))
        assert isinstance(heatmap, CoverageHeatmap)
        assert len(heatmap.cells) == 0
        assert heatmap.total_samples == 0

    def test_heatmap_with_data(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 10)
        mapper = CoverageMapper(populated_db)
        heatmap = mapper.get_heatmap((30.0, 31.0, -98.0, -97.0))
        assert heatmap.total_samples == 10
        assert len(heatmap.cells) > 0

    def test_heatmap_cells_have_aggregates(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 10)
        mapper = CoverageMapper(populated_db, grid_resolution_m=5000.0)
        heatmap = mapper.get_heatmap((30.0, 31.0, -98.0, -97.0))
        for cell in heatmap.cells:
            assert isinstance(cell, CoverageGridCell)
            assert cell.avg_rssi is not None
            assert cell.min_rssi <= cell.avg_rssi <= cell.max_rssi
            assert cell.sample_count > 0

    def test_heatmap_custom_resolution(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 10)
        mapper = CoverageMapper(populated_db)
        hm_fine = mapper.get_heatmap((30.0, 31.0, -98.0, -97.0), resolution=50.0)
        hm_coarse = mapper.get_heatmap((30.0, 31.0, -98.0, -97.0), resolution=5000.0)
        # Finer resolution → more cells (or equal if few samples)
        assert len(hm_fine.cells) >= len(hm_coarse.cells)

    def test_heatmap_bounds_filter(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 10)
        mapper = CoverageMapper(populated_db)
        # Tiny bounds that should exclude most samples
        heatmap = mapper.get_heatmap((30.267, 30.268, -97.744, -97.743))
        assert heatmap.total_samples < 10


# ── Grid Aggregation ────────────────────────────────────────────────


class TestGridAggregation:
    def test_single_cell(self, populated_db: MeshDatabase):
        """All samples in same location → one cell."""
        for _ in range(5):
            populated_db.add_coverage_sample(
                from_node="!aaa11111",
                to_node="!bbb22222",
                latitude=30.2672,
                longitude=-97.7431,
                rssi=-85.0,
            )
        mapper = CoverageMapper(populated_db)
        heatmap = mapper.get_heatmap((30.0, 31.0, -98.0, -97.0))
        assert len(heatmap.cells) == 1
        assert heatmap.cells[0].sample_count == 5
        assert heatmap.cells[0].avg_rssi == -85.0

    def test_multiple_cells(self, populated_db: MeshDatabase):
        """Widely spaced samples → multiple cells."""
        populated_db.add_coverage_sample("!a", "!b", 30.0, -97.0, -80.0)
        populated_db.add_coverage_sample("!a", "!b", 30.5, -97.5, -90.0)
        mapper = CoverageMapper(populated_db, grid_resolution_m=100.0)
        heatmap = mapper.get_heatmap((29.0, 31.0, -98.0, -96.0))
        assert len(heatmap.cells) == 2

    def test_snr_aggregation(self, populated_db: MeshDatabase):
        """SNR values are averaged per cell."""
        for snr in [10.0, 12.0, 8.0]:
            populated_db.add_coverage_sample("!a", "!b", 30.0, -97.0, -85.0, snr=snr)
        mapper = CoverageMapper(populated_db)
        heatmap = mapper.get_heatmap((29.0, 31.0, -98.0, -96.0))
        assert len(heatmap.cells) == 1
        assert abs(heatmap.cells[0].avg_snr - 10.0) < 0.01


# ── Dead Zones ──────────────────────────────────────────────────────


class TestDeadZones:
    def test_no_dead_zones_empty(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db)
        zones = mapper.get_dead_zones()
        assert zones == []

    def test_no_dead_zones_good_signal(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 5)  # RSSI -80 to -84
        mapper = CoverageMapper(populated_db)
        zones = mapper.get_dead_zones(min_rssi=-110)
        assert len(zones) == 0

    def test_dead_zone_detected(self, populated_db: MeshDatabase):
        _seed_dead_zone(populated_db)  # RSSI -115 to -119
        mapper = CoverageMapper(populated_db, grid_resolution_m=5000.0)
        zones = mapper.get_dead_zones(min_rssi=-110)
        assert len(zones) > 0
        for z in zones:
            assert z["avg_rssi"] < -110


# ── Coverage Stats ──────────────────────────────────────────────────


class TestCoverageStats:
    def test_empty_stats(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db)
        stats = mapper.get_coverage_stats()
        assert isinstance(stats, CoverageStats)
        assert stats.total_samples == 0

    def test_stats_with_data(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 10)
        mapper = CoverageMapper(populated_db)
        stats = mapper.get_coverage_stats()
        assert stats.total_samples == 10
        assert stats.avg_rssi is not None
        assert stats.min_rssi is not None
        assert stats.max_rssi is not None
        assert stats.min_rssi <= stats.avg_rssi <= stats.max_rssi


# ── GeoJSON Export ──────────────────────────────────────────────────


class TestGeoJSONExport:
    def test_empty_export(self, populated_db: MeshDatabase):
        mapper = CoverageMapper(populated_db)
        geojson = mapper.export_geojson((30.0, 31.0, -98.0, -97.0))
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 0

    def test_export_with_data(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 5)
        mapper = CoverageMapper(populated_db)
        geojson = mapper.export_geojson((30.0, 31.0, -98.0, -97.0))
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) > 0

        feature = geojson["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
        assert len(feature["geometry"]["coordinates"]) == 2
        assert "avg_rssi" in feature["properties"]
        assert "sample_count" in feature["properties"]

    def test_export_properties(self, populated_db: MeshDatabase):
        _seed_coverage(populated_db, 3)
        mapper = CoverageMapper(populated_db)
        geojson = mapper.export_geojson((30.0, 31.0, -98.0, -97.0))
        assert "bounds" in geojson["properties"]
        assert "resolution_m" in geojson["properties"]
        assert "total_samples" in geojson["properties"]
