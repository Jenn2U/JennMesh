"""Coverage mapper — aggregate RSSI observations into spatial heatmap grid.

Records signal observations from mesh neighbor reports and telemetry,
then produces grid-based heatmaps for coverage visualization.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.coverage import CoverageGridCell, CoverageHeatmap, CoverageStats

logger = logging.getLogger(__name__)

# Earth radius in meters (for coordinate math)
EARTH_RADIUS_M = 6_371_000


class CoverageMapper:
    """Aggregate signal observations into spatial heatmap grids.

    Usage:
        mapper = CoverageMapper(db, grid_resolution_m=100.0)
        mapper.record_observation("!aaa", "!bbb", 30.267, -97.743, -85.0, 10.5)
        heatmap = mapper.get_heatmap((30.0, 31.0, -98.0, -97.0))
    """

    def __init__(self, db: MeshDatabase, grid_resolution_m: float = 100.0):
        self.db = db
        self._grid_res = grid_resolution_m

    # ── Recording ────────────────────────────────────────────────────

    def record_observation(
        self,
        from_node: str,
        to_node: str,
        lat: float,
        lon: float,
        rssi: float,
        snr: Optional[float] = None,
    ) -> int:
        """Record a signal observation at a location. Returns sample ID."""
        return self.db.add_coverage_sample(
            from_node=from_node,
            to_node=to_node,
            latitude=lat,
            longitude=lon,
            rssi=rssi,
            snr=snr,
        )

    # ── Heatmap Generation ───────────────────────────────────────────

    def get_heatmap(
        self,
        bounds: tuple[float, float, float, float],
        resolution: Optional[float] = None,
    ) -> CoverageHeatmap:
        """Generate heatmap grid for bounding box.

        Args:
            bounds: (min_lat, max_lat, min_lon, max_lon)
            resolution: Grid cell size in meters (default: instance default)

        Returns:
            CoverageHeatmap with aggregated grid cells.
        """
        min_lat, max_lat, min_lon, max_lon = bounds
        res = resolution or self._grid_res

        samples = self.db.get_coverage_in_bounds(min_lat, max_lat, min_lon, max_lon)

        if not samples:
            return CoverageHeatmap(
                min_lat=min_lat,
                max_lat=max_lat,
                min_lon=min_lon,
                max_lon=max_lon,
                resolution_m=res,
            )

        # Aggregate samples into grid cells
        cells = self._aggregate_to_grid(samples, min_lat, min_lon, res)

        return CoverageHeatmap(
            min_lat=min_lat,
            max_lat=max_lat,
            min_lon=min_lon,
            max_lon=max_lon,
            resolution_m=res,
            cells=cells,
            total_samples=len(samples),
        )

    def _aggregate_to_grid(
        self,
        samples: list[dict],
        origin_lat: float,
        origin_lon: float,
        resolution_m: float,
    ) -> list[CoverageGridCell]:
        """Aggregate samples into grid cells keyed by (row, col)."""
        # Convert resolution to approximate degrees
        lat_deg_per_m = 1.0 / 111_320
        lon_deg_per_m = 1.0 / (111_320 * math.cos(math.radians(origin_lat)))
        lat_step = resolution_m * lat_deg_per_m
        lon_step = resolution_m * lon_deg_per_m

        # Group samples into grid cells
        grid: dict[tuple[int, int], list[dict]] = {}
        for s in samples:
            row = int((s["latitude"] - origin_lat) / lat_step)
            col = int((s["longitude"] - origin_lon) / lon_step)
            grid.setdefault((row, col), []).append(s)

        # Build CoverageGridCell for each occupied cell
        cells = []
        for (row, col), cell_samples in grid.items():
            rssi_values = [s["rssi"] for s in cell_samples]
            snr_values = [s["snr"] for s in cell_samples if s.get("snr") is not None]

            cells.append(
                CoverageGridCell(
                    lat_center=origin_lat + (row + 0.5) * lat_step,
                    lon_center=origin_lon + (col + 0.5) * lon_step,
                    avg_rssi=sum(rssi_values) / len(rssi_values),
                    min_rssi=min(rssi_values),
                    max_rssi=max(rssi_values),
                    sample_count=len(cell_samples),
                    avg_snr=(sum(snr_values) / len(snr_values)) if snr_values else None,
                )
            )

        return cells

    # ── Dead Zones ───────────────────────────────────────────────────

    def get_dead_zones(self, min_rssi: float = -110) -> list[dict]:
        """Identify grid cells with poor or no coverage.

        Returns cells where average RSSI is below the threshold.
        """
        stats = self.db.get_coverage_stats()
        total = stats.get("total_samples", 0)
        if total == 0:
            return []

        # Get all coverage in the full bounds
        # We need to find areas with poor signal, so we use the global bounds
        all_samples = self.db.get_coverage_in_bounds(-90.0, 90.0, -180.0, 180.0)
        if not all_samples:
            return []

        # Find bounds of all samples
        lats = [s["latitude"] for s in all_samples]
        lons = [s["longitude"] for s in all_samples]
        origin_lat = min(lats)
        origin_lon = min(lons)

        cells = self._aggregate_to_grid(all_samples, origin_lat, origin_lon, self._grid_res)

        return [
            {
                "lat_center": c.lat_center,
                "lon_center": c.lon_center,
                "avg_rssi": c.avg_rssi,
                "sample_count": c.sample_count,
            }
            for c in cells
            if c.avg_rssi < min_rssi
        ]

    # ── Statistics ───────────────────────────────────────────────────

    def get_coverage_stats(self) -> CoverageStats:
        """Fleet-wide coverage summary."""
        raw = self.db.get_coverage_stats()
        total = raw.get("total_samples") or 0
        if total == 0:
            return CoverageStats()

        dead_zones = self.get_dead_zones()

        return CoverageStats(
            total_samples=total,
            avg_rssi=raw.get("avg_rssi"),
            min_rssi=raw.get("min_rssi"),
            max_rssi=raw.get("max_rssi"),
            dead_zone_count=len(dead_zones),
            last_sample_at=raw.get("last_sample_at"),
        )

    # ── GeoJSON Export ───────────────────────────────────────────────

    def export_geojson(self, bounds: tuple[float, float, float, float]) -> dict:
        """Export coverage as GeoJSON FeatureCollection for external GIS tools."""
        heatmap = self.get_heatmap(bounds)
        features = []
        for cell in heatmap.cells:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [cell.lon_center, cell.lat_center],
                    },
                    "properties": {
                        "avg_rssi": cell.avg_rssi,
                        "min_rssi": cell.min_rssi,
                        "max_rssi": cell.max_rssi,
                        "sample_count": cell.sample_count,
                        "avg_snr": cell.avg_snr,
                    },
                }
            )

        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "bounds": list(bounds),
                "resolution_m": heatmap.resolution_m,
                "total_samples": heatmap.total_samples,
            },
        }
