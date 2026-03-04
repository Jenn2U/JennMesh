"""Coverage mapping models — signal strength observations and heatmap grids."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CoverageSample(BaseModel):
    """A single signal observation at a geographic location."""

    id: Optional[int] = Field(default=None, description="Auto-assigned by DB")
    from_node: str = Field(description="Transmitting node_id")
    to_node: str = Field(description="Receiving node_id")
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    rssi: float = Field(description="Received signal strength in dBm")
    snr: Optional[float] = Field(default=None, description="Signal-to-noise ratio in dB")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CoverageGridCell(BaseModel):
    """Aggregated coverage data for one spatial grid cell."""

    lat_center: float = Field(description="Grid cell center latitude")
    lon_center: float = Field(description="Grid cell center longitude")
    avg_rssi: float = Field(description="Average RSSI in dBm across samples")
    min_rssi: float = Field(description="Worst RSSI observed")
    max_rssi: float = Field(description="Best RSSI observed")
    sample_count: int = Field(default=0)
    avg_snr: Optional[float] = Field(default=None, description="Average SNR if available")


class CoverageHeatmap(BaseModel):
    """A grid of coverage cells within a geographic bounding box."""

    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    resolution_m: float = Field(description="Grid cell size in meters")
    cells: list[CoverageGridCell] = Field(default_factory=list)
    total_samples: int = Field(default=0)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class CoverageStats(BaseModel):
    """Fleet-wide coverage summary statistics."""

    total_samples: int = Field(default=0)
    unique_locations: int = Field(default=0, description="Distinct grid cells with data")
    avg_rssi: Optional[float] = Field(default=None)
    min_rssi: Optional[float] = Field(default=None, description="Worst RSSI observed fleet-wide")
    max_rssi: Optional[float] = Field(default=None, description="Best RSSI observed fleet-wide")
    coverage_area_estimate_m2: Optional[float] = Field(
        default=None, description="Approximate area covered (grid cells * cell area)"
    )
    dead_zone_count: int = Field(default=0, description="Cells below -110 dBm threshold")
    last_sample_at: Optional[datetime] = Field(default=None)
