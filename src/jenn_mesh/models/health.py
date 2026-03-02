"""Health and performance models — baselines, scoring, and grades."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class HealthGrade(str, Enum):
    """Health score classification."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class BaselineSnapshot(BaseModel):
    """Precomputed rolling baseline for a node's radio performance."""

    node_id: str
    rssi_mean: Optional[float] = None
    rssi_stddev: Optional[float] = None
    snr_mean: Optional[float] = None
    snr_stddev: Optional[float] = None
    battery_drain_rate: Optional[float] = Field(
        default=None, description="Volts per hour drain rate"
    )
    sample_count: int = 0
    window_start: Optional[str] = None
    window_end: Optional[str] = None

    @property
    def has_sufficient_data(self) -> bool:
        """Whether enough samples exist for meaningful baseline."""
        return self.sample_count >= 10


class DeviationReport(BaseModel):
    """Per-node deviation from baseline."""

    node_id: str
    rssi_deviation_sigma: Optional[float] = Field(
        default=None, description="RSSI deviation in standard deviations"
    )
    snr_deviation_sigma: Optional[float] = Field(
        default=None, description="SNR deviation in standard deviations"
    )
    is_degraded: bool = False
    details: list[str] = Field(default_factory=list)


class HealthScoreBreakdown(BaseModel):
    """Detailed breakdown of a node's health score."""

    node_id: str
    overall_score: float = Field(ge=0.0, le=100.0)
    grade: HealthGrade
    uptime_score: float = Field(ge=0.0, le=100.0)
    signal_score: float = Field(ge=0.0, le=100.0)
    battery_score: float = Field(ge=0.0, le=100.0)
    config_score: float = Field(ge=0.0, le=100.0)
    firmware_score: float = Field(ge=0.0, le=100.0)
    factors: dict[str, str] = Field(
        default_factory=dict,
        description="Human-readable note per factor explaining the score",
    )
