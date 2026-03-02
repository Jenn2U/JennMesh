"""Radio performance baselines — rolling 7-day per-node signal and battery metrics."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.health import BaselineSnapshot, DeviationReport

DEFAULT_WINDOW_DAYS = 7
DEFAULT_DEVIATION_THRESHOLD = 2.0  # standard deviations
MIN_SAMPLES_FOR_BASELINE = 10


class BaselineManager:
    """Computes and manages per-node rolling performance baselines."""

    def __init__(
        self,
        db: MeshDatabase,
        window_days: int = DEFAULT_WINDOW_DAYS,
        deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
    ):
        self._db = db
        self._window_days = window_days
        self._deviation_threshold = deviation_threshold

    def record_telemetry(
        self,
        node_id: str,
        *,
        rssi: Optional[int] = None,
        snr: Optional[float] = None,
        battery_level: Optional[int] = None,
        voltage: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        """Store a telemetry sample in telemetry_history."""
        self._db.add_telemetry_sample(
            node_id,
            rssi=rssi,
            snr=snr,
            battery_level=battery_level,
            voltage=voltage,
            timestamp=timestamp,
        )

    def recompute_baseline(self, node_id: str) -> Optional[BaselineSnapshot]:
        """Recompute rolling baseline from telemetry_history for the window.

        Returns BaselineSnapshot or None if insufficient samples.
        """
        since = (datetime.utcnow() - timedelta(days=self._window_days)).isoformat()
        samples = self._db.get_telemetry_history(node_id, since=since)

        if len(samples) < MIN_SAMPLES_FOR_BASELINE:
            return None

        rssi_vals = [s["rssi"] for s in samples if s["rssi"] is not None]
        snr_vals = [s["snr"] for s in samples if s["snr"] is not None]

        rssi_mean, rssi_stddev = _compute_stats(rssi_vals)
        snr_mean, snr_stddev = _compute_stats(snr_vals)

        # Compute battery drain rate from voltage series
        voltage_pairs = [
            (s["timestamp"], s["voltage"])
            for s in samples
            if s["voltage"] is not None and s["timestamp"] is not None
        ]
        drain_rate = _compute_drain_rate(voltage_pairs)

        timestamps = [s["timestamp"] for s in samples if s["timestamp"]]
        window_start = min(timestamps) if timestamps else None
        window_end = max(timestamps) if timestamps else None

        # Persist to DB
        self._db.upsert_baseline(
            node_id,
            rssi_mean=rssi_mean if rssi_vals else None,
            rssi_stddev=rssi_stddev if rssi_vals else None,
            snr_mean=snr_mean if snr_vals else None,
            snr_stddev=snr_stddev if snr_vals else None,
            battery_drain_rate=drain_rate,
            sample_count=len(samples),
            window_start=window_start,
            window_end=window_end,
        )

        return BaselineSnapshot(
            node_id=node_id,
            rssi_mean=rssi_mean if rssi_vals else None,
            rssi_stddev=rssi_stddev if rssi_vals else None,
            snr_mean=snr_mean if snr_vals else None,
            snr_stddev=snr_stddev if snr_vals else None,
            battery_drain_rate=drain_rate,
            sample_count=len(samples),
            window_start=window_start,
            window_end=window_end,
        )

    def get_baseline(self, node_id: str) -> Optional[BaselineSnapshot]:
        """Retrieve the precomputed baseline for a node."""
        row = self._db.get_baseline(node_id)
        if row is None:
            return None
        return BaselineSnapshot(
            node_id=row["node_id"],
            rssi_mean=row["rssi_mean"],
            rssi_stddev=row["rssi_stddev"],
            snr_mean=row["snr_mean"],
            snr_stddev=row["snr_stddev"],
            battery_drain_rate=row["battery_drain_rate"],
            sample_count=row["sample_count"],
            window_start=row["window_start"],
            window_end=row["window_end"],
        )

    def get_all_baselines(self) -> list[BaselineSnapshot]:
        """Get baselines for all devices."""
        rows = self._db.get_all_baselines()
        return [
            BaselineSnapshot(
                node_id=r["node_id"],
                rssi_mean=r["rssi_mean"],
                rssi_stddev=r["rssi_stddev"],
                snr_mean=r["snr_mean"],
                snr_stddev=r["snr_stddev"],
                battery_drain_rate=r["battery_drain_rate"],
                sample_count=r["sample_count"],
                window_start=r["window_start"],
                window_end=r["window_end"],
            )
            for r in rows
        ]

    def check_deviation(self, node_id: str) -> Optional[DeviationReport]:
        """Compare current device values against baseline.

        Returns DeviationReport or None if no baseline or device not found.
        """
        device = self._db.get_device(node_id)
        if device is None:
            return None

        baseline = self.get_baseline(node_id)
        if baseline is None or not baseline.has_sufficient_data:
            return None

        details: list[str] = []
        is_degraded = False

        rssi_sigma: Optional[float] = None
        if (
            baseline.rssi_mean is not None
            and baseline.rssi_stddev is not None
            and baseline.rssi_stddev > 0
            and device.get("signal_rssi") is not None
        ):
            rssi_sigma = (device["signal_rssi"] - baseline.rssi_mean) / baseline.rssi_stddev
            if abs(rssi_sigma) > self._deviation_threshold:
                is_degraded = True
                details.append(
                    f"RSSI {rssi_sigma:+.1f}σ from baseline "
                    f"(current={device['signal_rssi']}, mean={baseline.rssi_mean:.1f})"
                )

        snr_sigma: Optional[float] = None
        if (
            baseline.snr_mean is not None
            and baseline.snr_stddev is not None
            and baseline.snr_stddev > 0
            and device.get("signal_snr") is not None
        ):
            snr_sigma = (device["signal_snr"] - baseline.snr_mean) / baseline.snr_stddev
            if abs(snr_sigma) > self._deviation_threshold:
                is_degraded = True
                details.append(
                    f"SNR {snr_sigma:+.1f}σ from baseline "
                    f"(current={device['signal_snr']}, mean={baseline.snr_mean:.1f})"
                )

        return DeviationReport(
            node_id=node_id,
            rssi_deviation_sigma=round(rssi_sigma, 2) if rssi_sigma is not None else None,
            snr_deviation_sigma=round(snr_sigma, 2) if snr_sigma is not None else None,
            is_degraded=is_degraded,
            details=details,
        )

    def check_fleet_deviations(self) -> list[DeviationReport]:
        """Check all nodes for baseline deviations. Returns only degraded nodes."""
        devices = self._db.list_devices()
        deviations = []
        for device in devices:
            report = self.check_deviation(device["node_id"])
            if report is not None and report.is_degraded:
                deviations.append(report)
        return deviations

    def prune_old_telemetry(self, retention_days: int = 14) -> int:
        """Delete telemetry older than retention period."""
        return self._db.prune_old_telemetry(retention_days)


def _compute_stats(values: list[float]) -> tuple[float, float]:
    """Pure-Python mean and population stddev. No numpy."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return mean, math.sqrt(variance)


def _compute_drain_rate(
    voltage_pairs: list[tuple[str, float]],
) -> Optional[float]:
    """Compute battery drain rate in V/hour from (timestamp, voltage) pairs.

    Returns None if < 2 samples or < 1 hour elapsed.
    """
    if len(voltage_pairs) < 2:
        return None

    sorted_pairs = sorted(voltage_pairs, key=lambda p: p[0])
    first_ts, first_v = sorted_pairs[0]
    last_ts, last_v = sorted_pairs[-1]

    try:
        t0 = datetime.fromisoformat(first_ts)
        t1 = datetime.fromisoformat(last_ts)
    except (ValueError, TypeError):
        return None

    elapsed_hours = (t1 - t0).total_seconds() / 3600.0
    if elapsed_hours < 1.0:
        return None

    # Drain rate: positive means draining (voltage decreasing)
    return (first_v - last_v) / elapsed_hours
