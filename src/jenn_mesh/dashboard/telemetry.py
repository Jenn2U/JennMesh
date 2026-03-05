"""OpenTelemetry instrumentation for JennMesh dashboard.

Provides distributed tracing and custom metrics via OTLP export
to SigNoz (or any OTel-compatible backend).

No-op safe: when OTEL_EXPORTER_OTLP_ENDPOINT is unset, all
telemetry functions become no-ops. Safe to call multiple times.

Usage:
    from jenn_mesh.dashboard.telemetry import init_telemetry

    # Call once at app startup (in create_app)
    init_telemetry()
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_telemetry() -> bool:
    """Initialize OpenTelemetry tracing and metrics.

    Reads ``OTEL_EXPORTER_OTLP_ENDPOINT`` from environment.
    When unset, telemetry is disabled (no-op mode).

    Returns:
        True if telemetry was initialized, False if running in no-op mode.
    """
    global _initialized

    if _initialized:
        return False

    _initialized = True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    service_name = os.environ.get("OTEL_SERVICE_NAME", "jenn-mesh-dashboard")

    if not endpoint:
        logger.info("No OTEL_EXPORTER_OTLP_ENDPOINT; telemetry disabled (no-op mode)")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        # Auto-instrument FastAPI — will instrument any FastAPI app created after this
        FastAPIInstrumentor.instrument()

        logger.info("OpenTelemetry initialized: endpoint=%s service=%s", endpoint, service_name)
        return True

    except ImportError:
        logger.warning(
            "OpenTelemetry packages not installed; telemetry disabled. "
            "Install with: pip install jenn-mesh[telemetry]"
        )
        return False
    except Exception:
        logger.exception("Failed to initialize OpenTelemetry")
        return False
