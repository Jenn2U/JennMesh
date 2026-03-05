"""Tests for OpenTelemetry instrumentation in JennMesh dashboard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestInitTelemetry:
    """Test init_telemetry() function."""

    def setup_method(self) -> None:
        """Reset the module-level _initialized flag before each test."""
        import jenn_mesh.dashboard.telemetry as mod

        mod._initialized = False

    def test_noop_when_no_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Telemetry disabled when OTEL_EXPORTER_OTLP_ENDPOINT is not set."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        from jenn_mesh.dashboard.telemetry import init_telemetry

        result = init_telemetry()
        assert result is False

    def test_noop_when_endpoint_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Telemetry disabled when OTEL_EXPORTER_OTLP_ENDPOINT is empty string."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

        from jenn_mesh.dashboard.telemetry import init_telemetry

        result = init_telemetry()
        assert result is False

    @patch("jenn_mesh.dashboard.telemetry.logger")
    def test_noop_logs_message(
        self, mock_logger: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No-op mode logs an info message."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        from jenn_mesh.dashboard.telemetry import init_telemetry

        init_telemetry()
        mock_logger.info.assert_called_once()
        assert "no-op" in mock_logger.info.call_args[0][0].lower()

    def test_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling init_telemetry() twice returns False on second call."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        from jenn_mesh.dashboard.telemetry import init_telemetry

        init_telemetry()
        result = init_telemetry()
        assert result is False

    @patch("jenn_mesh.dashboard.telemetry.logger")
    def test_import_error_graceful(
        self, mock_logger: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Graceful fallback when OTel packages are not installed."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")

        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if "opentelemetry" in name:
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from jenn_mesh.dashboard.telemetry import init_telemetry

            result = init_telemetry()

        assert result is False
        mock_logger.warning.assert_called_once()

    def test_init_with_endpoint_calls_otel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When endpoint is set and OTel is available, initialization succeeds."""
        import sys

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
        monkeypatch.setenv("OTEL_SERVICE_NAME", "test-mesh")

        mock_provider = MagicMock()
        mock_instrumentor = MagicMock()

        # Build fake OTel module hierarchy so lazy imports inside
        # init_telemetry() resolve without the real packages installed.
        mock_trace = MagicMock()
        mock_sdk_trace = MagicMock()
        mock_sdk_trace.TracerProvider.return_value = mock_provider
        mock_sdk_export = MagicMock()
        mock_sdk_resources = MagicMock()
        mock_exporter_mod = MagicMock()
        mock_instr_mod = MagicMock()
        mock_instr_mod.FastAPIInstrumentor = mock_instrumentor

        fake_modules = {
            "opentelemetry": MagicMock(),
            "opentelemetry.trace": mock_trace,
            "opentelemetry.sdk": MagicMock(),
            "opentelemetry.sdk.trace": mock_sdk_trace,
            "opentelemetry.sdk.trace.export": mock_sdk_export,
            "opentelemetry.sdk.resources": mock_sdk_resources,
            "opentelemetry.exporter": MagicMock(),
            "opentelemetry.exporter.otlp": MagicMock(),
            "opentelemetry.exporter.otlp.proto": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": mock_exporter_mod,
            "opentelemetry.instrumentation": MagicMock(),
            "opentelemetry.instrumentation.fastapi": mock_instr_mod,
        }

        with patch.dict(sys.modules, fake_modules):
            from jenn_mesh.dashboard.telemetry import init_telemetry

            result = init_telemetry()

        assert result is True
        mock_provider.add_span_processor.assert_called_once()
        mock_instrumentor.instrument.assert_called_once()
