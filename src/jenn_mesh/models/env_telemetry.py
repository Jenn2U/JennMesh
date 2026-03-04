"""Environmental telemetry models — temp, humidity, pressure, air quality."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class EnvReading(BaseModel):
    """A single environmental sensor reading from a mesh node."""

    id: Optional[int] = Field(default=None, description="Auto-assigned by DB")
    node_id: str = Field(description="Source mesh node_id")
    temperature: Optional[float] = Field(default=None, description="Temperature in degrees Celsius")
    humidity: Optional[float] = Field(
        default=None, description="Relative humidity percentage (0-100)"
    )
    pressure: Optional[float] = Field(default=None, description="Barometric pressure in hPa")
    air_quality: Optional[int] = Field(default=None, description="Air quality index (0-500)")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EnvThreshold(BaseModel):
    """Configurable threshold for environmental alerts."""

    metric: str = Field(description="Metric name: temperature, humidity, pressure, air_quality")
    min_value: Optional[float] = Field(default=None, description="Alert below this")
    max_value: Optional[float] = Field(default=None, description="Alert above this")
    enabled: bool = Field(default=True)


class EnvAlert(BaseModel):
    """Alert triggered by environmental threshold breach."""

    node_id: str
    metric: str
    value: float
    threshold_min: Optional[float] = None
    threshold_max: Optional[float] = None
    message: str
