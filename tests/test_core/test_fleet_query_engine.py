"""Tests for FleetQueryEngine (MESH-046)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jenn_mesh.core.fleet_query_engine import (
    MAX_STEPS,
    FleetQueryEngine,
    _is_offline,
    _summarize_device,
    _version_lt,
)
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet_query import (
    CANNED_QUERIES,
    FleetQueryResponse,
    QueryFunction,
    QueryPlan,
    QueryStep,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def engine(populated_db: MeshDatabase) -> FleetQueryEngine:
    """Engine without Ollama (keyword-only mode)."""
    return FleetQueryEngine(db=populated_db)


@pytest.fixture
def mock_ollama() -> MagicMock:
    """Mock OllamaClient with is_available, chat, chat_json."""
    mock = MagicMock()
    mock.is_available = AsyncMock(return_value=True)
    mock.chat = AsyncMock(return_value="Test answer from Ollama.")
    mock.chat_json = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def engine_with_ollama(populated_db: MeshDatabase, mock_ollama: MagicMock) -> FleetQueryEngine:
    """Engine with mocked Ollama."""
    return FleetQueryEngine(db=populated_db, ollama=mock_ollama)


# ── Constructor ───────────────────────────────────────────────────────


class TestEngineInit:
    def test_init_without_ollama(self, populated_db: MeshDatabase) -> None:
        engine = FleetQueryEngine(db=populated_db)
        assert engine._ollama is None
        assert engine.db is populated_db

    def test_init_with_ollama(self, populated_db: MeshDatabase) -> None:
        mock = MagicMock()
        engine = FleetQueryEngine(db=populated_db, ollama=mock)
        assert engine._ollama is mock

    def test_get_status(self, engine: FleetQueryEngine) -> None:
        status = engine.get_status()
        assert status["engine"] == "fleet_query"
        assert status["ollama_configured"] is False
        assert status["canned_queries"] == len(CANNED_QUERIES)


# ── Version comparison ────────────────────────────────────────────────


class TestVersionComparison:
    def test_version_lt_true(self) -> None:
        assert _version_lt("2.4.2", "2.5") is True
        assert _version_lt("2.4.2", "2.5.0") is True
        assert _version_lt("1.0.0", "2.0.0") is True

    def test_version_lt_false(self) -> None:
        assert _version_lt("2.5.6", "2.5") is False
        assert _version_lt("3.0.0", "2.5.0") is False

    def test_version_lt_equal(self) -> None:
        assert _version_lt("2.5.0", "2.5.0") is False

    def test_version_lt_bad_input(self) -> None:
        assert _version_lt("unknown", "2.5") is False
        assert _version_lt("", "") is False


# ── Offline detection ─────────────────────────────────────────────────


class TestOfflineDetection:
    def test_none_last_seen_is_offline(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        assert _is_offline({"last_seen": None}, now, timedelta(seconds=600)) is True

    def test_recent_device_is_online(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=2)).isoformat()
        assert _is_offline({"last_seen": recent}, now, timedelta(seconds=600)) is False

    def test_old_device_is_offline(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=2)).isoformat()
        assert _is_offline({"last_seen": old}, now, timedelta(seconds=600)) is True


# ── Device summarizer ─────────────────────────────────────────────────


class TestSummarizeDevice:
    def test_summarize_full(self) -> None:
        d = {
            "node_id": "!aaa11111",
            "long_name": "Relay-HQ",
            "role": "ROUTER",
            "firmware_version": "2.5.6",
            "battery_level": 80,
            "signal_rssi": -85,
            "signal_snr": 10.5,
            "last_seen": "2026-01-01T00:00:00",
            "mesh_status": "reachable",
            "latitude": 30.2672,
            "longitude": -97.7431,
        }
        result = _summarize_device(d)
        assert result["node_id"] == "!aaa11111"
        assert result["name"] == "Relay-HQ"
        assert result["battery"] == 80

    def test_summarize_minimal(self) -> None:
        d = {"node_id": "!xyz"}
        result = _summarize_device(d)
        assert result["node_id"] == "!xyz"
        assert result["name"] == "!xyz"  # Falls back to node_id


# ── Plan validation ───────────────────────────────────────────────────


class TestPlanValidation:
    def test_valid_single_step(self, engine: FleetQueryEngine) -> None:
        raw = {
            "steps": [{"function": "find_devices", "params": {"status": "offline"}}],
            "reasoning": "test",
        }
        plan = engine._validate_plan(raw)
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].function == QueryFunction.FIND_DEVICES

    def test_empty_steps_returns_none(self, engine: FleetQueryEngine) -> None:
        assert engine._validate_plan({"steps": []}) is None

    def test_unknown_function_skipped(self, engine: FleetQueryEngine) -> None:
        raw = {
            "steps": [{"function": "drop_all_tables", "params": {}}],
        }
        plan = engine._validate_plan(raw)
        assert plan is None  # All steps invalid → None

    def test_max_steps_capped(self, engine: FleetQueryEngine) -> None:
        raw = {
            "steps": [
                {"function": "get_fleet_summary", "params": {}},
                {"function": "find_devices", "params": {}},
                {"function": "get_active_alerts", "params": {}},
                {"function": "find_spof_nodes", "params": {}},  # 4th → trimmed
            ],
        }
        plan = engine._validate_plan(raw)
        assert plan is not None
        assert len(plan.steps) == MAX_STEPS

    def test_mixed_valid_invalid(self, engine: FleetQueryEngine) -> None:
        raw = {
            "steps": [
                {"function": "find_devices", "params": {}},
                {"function": "INVALID", "params": {}},
                {"function": "get_fleet_summary", "params": {}},
            ],
        }
        plan = engine._validate_plan(raw)
        assert plan is not None
        assert len(plan.steps) == 2  # Invalid step skipped


# ── Plan execution ────────────────────────────────────────────────────


class TestPlanExecution:
    def test_find_devices_all(self, engine: FleetQueryEngine) -> None:
        """find_devices with no filters returns all 4 test devices."""
        plan = QueryPlan(steps=[QueryStep(function=QueryFunction.FIND_DEVICES, params={})])
        result = engine._execute_plan(plan)
        key = "step_0_find_devices"
        assert key in result
        assert len(result[key]) == 4

    def test_find_devices_offline(self, engine: FleetQueryEngine) -> None:
        """Offline filter: !ccc33333 (2h old) + !ddd44444 (never seen)."""
        plan = QueryPlan(
            steps=[
                QueryStep(
                    function=QueryFunction.FIND_DEVICES,
                    params={"status": "offline"},
                )
            ]
        )
        result = engine._execute_plan(plan)
        devices = result["step_0_find_devices"]
        node_ids = {d["node_id"] for d in devices}
        assert "!ccc33333" in node_ids
        assert "!ddd44444" in node_ids
        assert "!aaa11111" not in node_ids

    def test_find_devices_battery_below(self, engine: FleetQueryEngine) -> None:
        """Battery < 20 should find !ccc33333 (15%)."""
        plan = QueryPlan(
            steps=[
                QueryStep(
                    function=QueryFunction.FIND_DEVICES,
                    params={"battery_below": 20},
                )
            ]
        )
        result = engine._execute_plan(plan)
        devices = result["step_0_find_devices"]
        assert len(devices) == 1
        assert devices[0]["node_id"] == "!ccc33333"

    def test_find_devices_firmware_lt(self, engine: FleetQueryEngine) -> None:
        """firmware < 2.5 should find !ccc33333 (2.4.2)."""
        plan = QueryPlan(
            steps=[
                QueryStep(
                    function=QueryFunction.FIND_DEVICES,
                    params={"firmware_lt": "2.5"},
                )
            ]
        )
        result = engine._execute_plan(plan)
        devices = result["step_0_find_devices"]
        assert len(devices) == 1
        assert devices[0]["node_id"] == "!ccc33333"

    def test_find_devices_name_contains(self, engine: FleetQueryEngine) -> None:
        """name_contains 'relay' should find !aaa11111 (Relay-HQ)."""
        plan = QueryPlan(
            steps=[
                QueryStep(
                    function=QueryFunction.FIND_DEVICES,
                    params={"name_contains": "relay"},
                )
            ]
        )
        result = engine._execute_plan(plan)
        devices = result["step_0_find_devices"]
        assert len(devices) == 1
        assert devices[0]["name"] == "Relay-HQ"

    def test_find_devices_role(self, engine: FleetQueryEngine) -> None:
        """Role=ROUTER should find !aaa11111."""
        plan = QueryPlan(
            steps=[
                QueryStep(
                    function=QueryFunction.FIND_DEVICES,
                    params={"role": "ROUTER"},
                )
            ]
        )
        result = engine._execute_plan(plan)
        devices = result["step_0_find_devices"]
        assert len(devices) == 1
        assert devices[0]["node_id"] == "!aaa11111"

    def test_fleet_summary(self, engine: FleetQueryEngine) -> None:
        plan = QueryPlan(steps=[QueryStep(function=QueryFunction.GET_FLEET_SUMMARY, params={})])
        result = engine._execute_plan(plan)
        summary = result["step_0_get_fleet_summary"]
        assert "total_devices" in summary
        assert summary["total_devices"] == 4

    def test_active_alerts(self, engine: FleetQueryEngine) -> None:
        plan = QueryPlan(steps=[QueryStep(function=QueryFunction.GET_ACTIVE_ALERTS, params={})])
        result = engine._execute_plan(plan)
        # populated_db doesn't create explicit alerts, so may be empty
        assert "step_0_get_active_alerts" in result

    def test_mesh_topology(self, engine: FleetQueryEngine) -> None:
        plan = QueryPlan(steps=[QueryStep(function=QueryFunction.GET_MESH_TOPOLOGY, params={})])
        result = engine._execute_plan(plan)
        topo = result["step_0_get_mesh_topology"]
        assert topo["total_edges"] == 3  # 3 edges seeded in populated_db
        assert "!ddd44444" in topo["isolated_nodes"]

    def test_find_spof(self, engine: FleetQueryEngine) -> None:
        plan = QueryPlan(steps=[QueryStep(function=QueryFunction.FIND_SPOF_NODES, params={})])
        result = engine._execute_plan(plan)
        spof = result["step_0_find_spof_nodes"]
        # !bbb22222 is the only path between relay↔mobile
        assert "!bbb22222" in spof

    def test_offline_transitions(self, engine: FleetQueryEngine) -> None:
        """!ccc33333 went offline 2h ago, within 24h window."""
        plan = QueryPlan(
            steps=[
                QueryStep(
                    function=QueryFunction.GET_OFFLINE_TRANSITIONS,
                    params={"hours": 24},
                )
            ]
        )
        result = engine._execute_plan(plan)
        transitions = result["step_0_get_offline_transitions"]
        node_ids = {d["node_id"] for d in transitions}
        assert "!ccc33333" in node_ids

    def test_chained_steps(self, engine: FleetQueryEngine) -> None:
        """Multi-step plan executes both steps."""
        plan = QueryPlan(
            steps=[
                QueryStep(function=QueryFunction.GET_FLEET_SUMMARY, params={}),
                QueryStep(function=QueryFunction.FIND_DEVICES, params={"status": "offline"}),
            ]
        )
        result = engine._execute_plan(plan)
        assert "step_0_get_fleet_summary" in result
        assert "step_1_find_devices" in result


# ── Keyword fallback ──────────────────────────────────────────────────


class TestKeywordFallback:
    def test_offline_keyword(self, engine: FleetQueryEngine) -> None:
        result = engine._keyword_fallback("which nodes are offline?")
        assert result is not None
        assert result.source == "keyword"
        # _simple_format uses device names, not node_ids
        assert "Mobile-Field" in result.answer or "Sensor-Env" in result.answer

    def test_battery_keyword(self, engine: FleetQueryEngine) -> None:
        result = engine._keyword_fallback("show me low battery devices")
        assert result is not None
        assert result.source == "keyword"

    def test_health_keyword(self, engine: FleetQueryEngine) -> None:
        result = engine._keyword_fallback("fleet health summary")
        assert result is not None
        assert result.source == "keyword"

    def test_topology_keyword(self, engine: FleetQueryEngine) -> None:
        result = engine._keyword_fallback("show network topology")
        assert result is not None

    def test_spof_keyword(self, engine: FleetQueryEngine) -> None:
        result = engine._keyword_fallback("find single points of failure")
        assert result is not None

    def test_no_match(self, engine: FleetQueryEngine) -> None:
        result = engine._keyword_fallback("xyzzy plugh nonsense")
        assert result is None


# ── Canned response ───────────────────────────────────────────────────


class TestCannedResponse:
    def test_canned_lists_queries(self, engine: FleetQueryEngine) -> None:
        result = engine._canned_response("???")
        assert result.source == "canned"
        assert "couldn't understand" in result.answer.lower()
        # Should list suggestions
        assert "offline" in result.answer.lower()


# ── Full ask() flow ───────────────────────────────────────────────────


class TestAskFlow:
    @pytest.mark.asyncio
    async def test_ask_keyword_no_ollama(self, engine: FleetQueryEngine) -> None:
        """Without Ollama, ask() uses keyword fallback."""
        result = await engine.ask("which nodes are offline?")
        assert isinstance(result, FleetQueryResponse)
        assert result.source == "keyword"
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_ask_canned_no_ollama(self, engine: FleetQueryEngine) -> None:
        """Unmatched question without Ollama → canned response."""
        result = await engine.ask("xyzzy plugh nonsense")
        assert result.source == "canned"

    @pytest.mark.asyncio
    async def test_ask_ollama_success(
        self, engine_with_ollama: FleetQueryEngine, mock_ollama: MagicMock
    ) -> None:
        """With Ollama returning valid plan → ollama source."""
        mock_ollama.chat_json = AsyncMock(
            return_value={
                "steps": [{"function": "get_fleet_summary", "params": {}}],
                "reasoning": "User asked for fleet overview",
            }
        )
        result = await engine_with_ollama.ask("how's the fleet doing?")
        assert result.source == "ollama"
        assert result.ollama_available is True
        assert result.query_plan is not None

    @pytest.mark.asyncio
    async def test_ask_ollama_bad_json_falls_to_keyword(
        self, engine_with_ollama: FleetQueryEngine, mock_ollama: MagicMock
    ) -> None:
        """Ollama returns garbage JSON → falls to keyword fallback."""
        mock_ollama.chat_json = AsyncMock(return_value=None)
        result = await engine_with_ollama.ask("which nodes are offline?")
        assert result.source == "keyword"

    @pytest.mark.asyncio
    async def test_ask_ollama_unavailable_falls_to_keyword(
        self, engine_with_ollama: FleetQueryEngine, mock_ollama: MagicMock
    ) -> None:
        """Ollama not available → keyword fallback."""
        mock_ollama.is_available = AsyncMock(return_value=False)
        result = await engine_with_ollama.ask("fleet health summary")
        assert result.source == "keyword"
        assert result.ollama_available is False


# ── Query history ─────────────────────────────────────────────────────


class TestQueryHistory:
    @pytest.mark.asyncio
    async def test_history_persisted(self, engine: FleetQueryEngine) -> None:
        """After ask(), entry appears in get_history()."""
        await engine.ask("fleet health summary")
        history = engine.get_history(limit=5)
        assert len(history) >= 1
        assert history[0]["question"] == "fleet health summary"

    @pytest.mark.asyncio
    async def test_history_empty_initially(self, populated_db: MeshDatabase) -> None:
        """Fresh engine has empty history."""
        engine = FleetQueryEngine(db=populated_db)
        history = engine.get_history()
        assert history == []
