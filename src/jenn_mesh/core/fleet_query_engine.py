"""Fleet query engine — natural language fleet queries via Ollama (MESH-046).

Two-pass LLM architecture:
1. Parse: NL question → Ollama chat_json() → QueryPlan
2. Execute: Dispatch validated steps against existing DB/registry/topology methods
3. Format: Raw results + question → Ollama chat() → conversational answer

When Ollama is unavailable, falls back to keyword matching then canned queries.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet_query import (
    CANNED_QUERIES,
    FleetQueryResponse,
    QueryFunction,
    QueryPlan,
    QueryStep,
)

logger = logging.getLogger(__name__)

# ── System prompts (module constants, not generated) ──────────────────

PARSE_SYSTEM_PROMPT = """\
You are a Meshtastic fleet query planner. Convert the user's natural language \
question into a JSON query plan.

Available functions:

1. find_devices(filters) — Filter fleet devices.
   params: role (str, e.g. "ROUTER","CLIENT","SENSOR"), \
firmware_lt/firmware_gt (str, semver like "2.5"), \
battery_below/battery_above (int, 0-100), \
status ("online"|"offline"), name_contains (str, partial match on long_name), \
near_lat/near_lon (float), near_radius_degrees (float, ~0.01 = 1km), \
last_seen_hours (int, devices seen within last N hours)

2. get_fleet_summary() — Aggregate fleet health stats. No params.

3. get_active_alerts(severity?, alert_type?) — Active unresolved alerts.
   params: severity ("critical"|"warning"|"info"), \
alert_type (e.g. "node_offline","low_battery","config_drift")

4. get_device_telemetry(node_id, metric, hours) — Time-series data for one device.
   params: node_id (str, e.g. "!aaa11111"), metric ("battery"|"rssi"|"snr"|"voltage"), \
hours (int, lookback window)

5. get_mesh_topology() — Full network graph with edges and metrics. No params.

6. find_spof_nodes() — Single points of failure in the mesh. No params.

7. get_device_history(node_id, hours) — Timeline of alerts for one device.
   params: node_id (str), hours (int, default 24)

8. get_offline_transitions(hours) — Nodes that went offline recently.
   params: hours (int, default 24)

If the user refers to a device by name (e.g. "warehouse relay"), use the \
name_contains param in find_devices. If unsure, default to get_fleet_summary.

Respond ONLY with JSON: \
{"steps": [{"function": "...", "params": {...}, "description": "..."}], \
"reasoning": "..."}
Maximum 3 steps. Keep it simple — prefer 1 step when possible."""

FORMAT_SYSTEM_PROMPT = """\
You are a Meshtastic fleet assistant. Given the user's question and \
raw query results, provide a concise conversational answer.

Rules:
- Use specific numbers and device names/IDs from the results.
- Format device lists clearly (one per line if multiple).
- If no results match, say so clearly.
- Keep answers under 150 words.
- Do NOT include raw JSON in your answer.
- Do NOT use markdown headers — just plain text with line breaks."""

# ── Keyword fallback patterns ─────────────────────────────────────────

_KEYWORD_PATTERNS: list[tuple[list[str], QueryFunction, dict[str, Any]]] = [
    (["offline", "down", "unreachable"], QueryFunction.FIND_DEVICES, {"status": "offline"}),
    (["online", "active", "up"], QueryFunction.FIND_DEVICES, {"status": "online"}),
    (["battery", "low battery", "charge"], QueryFunction.FIND_DEVICES, {"battery_below": 20}),
    (["alert", "critical"], QueryFunction.GET_ACTIVE_ALERTS, {"severity": "critical"}),
    (["alert", "warning"], QueryFunction.GET_ACTIVE_ALERTS, {"severity": "warning"}),
    (["health", "summary", "overview", "status"], QueryFunction.GET_FLEET_SUMMARY, {}),
    (["topology", "network", "graph", "connectivity"], QueryFunction.GET_MESH_TOPOLOGY, {}),
    (
        ["spof", "single point", "failure", "articulation"],
        QueryFunction.FIND_SPOF_NODES,
        {},
    ),
    (["drift", "config"], QueryFunction.FIND_DEVICES, {}),
    (
        ["went offline", "came online", "transition"],
        QueryFunction.GET_OFFLINE_TRANSITIONS,
        {"hours": 24},
    ),
]

# Maximum allowed steps per query plan
MAX_STEPS = 3


class FleetQueryEngine:
    """Natural language fleet query engine — Ollama + keyword fallback.

    Usage:
        engine = FleetQueryEngine(db, ollama_client)
        result = await engine.ask("which nodes are offline?")
    """

    def __init__(self, db: MeshDatabase, ollama: object = None):
        self.db = db
        self._ollama = ollama  # OllamaClient or None
        # Lazy imports for registry/topology (avoid circular deps)
        self._registry: Any = None
        self._topology: Any = None

    def _get_registry(self) -> Any:
        if self._registry is None:
            from jenn_mesh.core.registry import DeviceRegistry

            self._registry = DeviceRegistry(self.db)
        return self._registry

    def _get_topology(self) -> Any:
        if self._topology is None:
            from jenn_mesh.core.topology import TopologyManager

            self._topology = TopologyManager(self.db)
        return self._topology

    # ── Public API ────────────────────────────────────────────────────

    async def ask(self, question: str) -> FleetQueryResponse:
        """Main entry point: NL question → structured answer."""
        start = time.monotonic()
        ollama_available = False

        # 1. Try Ollama two-pass if available
        if self._ollama is not None:
            try:
                available = await self._ollama.is_available()
                ollama_available = available
                if available:
                    result = await self._ollama_two_pass(question)
                    if result is not None:
                        result.duration_ms = int((time.monotonic() - start) * 1000)
                        result.ollama_available = True
                        self._log_query(question, result)
                        return result
            except Exception:
                logger.exception("Ollama two-pass failed for query: %s", question[:80])

        # 2. Keyword fallback
        result = self._keyword_fallback(question)
        if result is not None:
            result.duration_ms = int((time.monotonic() - start) * 1000)
            result.ollama_available = ollama_available
            self._log_query(question, result)
            return result

        # 3. Canned query menu
        result = self._canned_response(question)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.ollama_available = ollama_available
        self._log_query(question, result)
        return result

    def get_status(self) -> dict[str, Any]:
        """Return engine availability info."""
        return {
            "engine": "fleet_query",
            "ollama_configured": self._ollama is not None,
            "keyword_patterns": len(_KEYWORD_PATTERNS),
            "canned_queries": len(CANNED_QUERIES),
        }

    def get_history(self, limit: int = 20) -> list[dict]:
        """Return recent query log entries."""
        return self.db.get_nl_query_history(limit=limit)

    # ── Ollama two-pass ───────────────────────────────────────────────

    async def _ollama_two_pass(self, question: str) -> Optional[FleetQueryResponse]:
        """Pass 1: parse → plan. Execute. Pass 2: format answer."""
        # Pass 1: parse question into QueryPlan
        raw_plan = await self._ollama.chat_json(PARSE_SYSTEM_PROMPT, question)
        if raw_plan is None:
            return None

        plan = self._validate_plan(raw_plan)
        if plan is None:
            return None

        # Execute the plan
        raw_data = self._execute_plan(plan)

        # Pass 2: format results into conversational answer
        format_input = json.dumps(
            {"question": question, "results": raw_data},
            indent=2,
            default=str,
        )
        answer = await self._ollama.chat(FORMAT_SYSTEM_PROMPT, format_input)

        if answer is None:
            # Ollama format pass failed — build a simple summary
            answer = self._simple_format(question, raw_data)

        return FleetQueryResponse(
            question=question,
            answer=answer,
            source="ollama",
            query_plan=plan,
            raw_data=raw_data,
        )

    # ── Plan validation ───────────────────────────────────────────────

    def _validate_plan(self, raw: dict[str, Any]) -> Optional[QueryPlan]:
        """Validate raw LLM JSON into a QueryPlan, or None if invalid."""
        try:
            steps_raw = raw.get("steps", [])
            if not isinstance(steps_raw, list) or len(steps_raw) == 0:
                return None
            if len(steps_raw) > MAX_STEPS:
                steps_raw = steps_raw[:MAX_STEPS]

            steps = []
            for s in steps_raw:
                func_name = s.get("function", "")
                # Validate against enum allowlist
                try:
                    func = QueryFunction(func_name)
                except ValueError:
                    logger.warning("LLM proposed unknown function: %s", func_name)
                    continue
                steps.append(
                    QueryStep(
                        function=func,
                        params=s.get("params", {}),
                        description=s.get("description", ""),
                    )
                )

            if not steps:
                return None

            return QueryPlan(
                steps=steps,
                reasoning=raw.get("reasoning", ""),
            )
        except Exception:
            logger.exception("Failed to validate query plan")
            return None

    # ── Plan execution ────────────────────────────────────────────────

    def _execute_plan(self, plan: QueryPlan) -> dict[str, Any]:
        """Execute a validated query plan, returning aggregated results."""
        results: dict[str, Any] = {}
        for i, step in enumerate(plan.steps):
            key = f"step_{i}_{step.function.value}"
            try:
                results[key] = self._execute_step(step, results)
            except Exception:
                logger.exception("Step %d (%s) failed", i, step.function.value)
                results[key] = {"error": f"Step failed: {step.function.value}"}
        return results

    def _execute_step(self, step: QueryStep, prior: dict[str, Any]) -> Any:
        """Execute a single query step. Dispatches to internal methods."""
        dispatch = {
            QueryFunction.FIND_DEVICES: self._exec_find_devices,
            QueryFunction.GET_FLEET_SUMMARY: self._exec_fleet_summary,
            QueryFunction.GET_ACTIVE_ALERTS: self._exec_active_alerts,
            QueryFunction.GET_DEVICE_TELEMETRY: self._exec_device_telemetry,
            QueryFunction.GET_MESH_TOPOLOGY: self._exec_mesh_topology,
            QueryFunction.FIND_SPOF_NODES: self._exec_find_spof,
            QueryFunction.GET_DEVICE_HISTORY: self._exec_device_history,
            QueryFunction.GET_OFFLINE_TRANSITIONS: self._exec_offline_transitions,
        }
        handler = dispatch.get(step.function)
        if handler is None:
            return {"error": f"Unknown function: {step.function}"}
        return handler(step.params)

    # ── Dispatch handlers ─────────────────────────────────────────────

    def _exec_find_devices(self, params: dict[str, Any]) -> list[dict]:
        """Filter fleet devices by role, firmware, battery, status, position, name."""
        devices = self.db.list_devices()

        if "role" in params:
            role_val = str(params["role"]).upper()
            devices = [d for d in devices if (d.get("role") or "").upper() == role_val]

        if "firmware_lt" in params:
            target = str(params["firmware_lt"])
            devices = [
                d for d in devices if _version_lt(d.get("firmware_version", "0.0.0"), target)
            ]

        if "firmware_gt" in params:
            target = str(params["firmware_gt"])
            devices = [
                d for d in devices if _version_lt(target, d.get("firmware_version", "0.0.0"))
            ]

        if "battery_below" in params:
            threshold = int(params["battery_below"])
            devices = [
                d
                for d in devices
                if d.get("battery_level") is not None and d["battery_level"] < threshold
            ]

        if "battery_above" in params:
            threshold = int(params["battery_above"])
            devices = [
                d
                for d in devices
                if d.get("battery_level") is not None and d["battery_level"] > threshold
            ]

        if "status" in params:
            status = str(params["status"]).lower()
            now = datetime.now(timezone.utc)
            threshold = timedelta(seconds=600)
            if status == "offline":
                devices = [d for d in devices if _is_offline(d, now, threshold)]
            elif status == "online":
                devices = [d for d in devices if not _is_offline(d, now, threshold)]

        if "name_contains" in params:
            pattern = str(params["name_contains"]).lower()
            devices = [
                d
                for d in devices
                if pattern in (d.get("long_name") or "").lower()
                or pattern in (d.get("short_name") or "").lower()
            ]

        if "near_lat" in params and "near_lon" in params:
            lat = float(params["near_lat"])
            lon = float(params["near_lon"])
            radius = float(params.get("near_radius_degrees", 0.01))
            devices = [
                d
                for d in devices
                if d.get("latitude") is not None
                and d.get("longitude") is not None
                and abs(d["latitude"] - lat) <= radius
                and abs(d["longitude"] - lon) <= radius
            ]

        if "last_seen_hours" in params:
            hours = int(params["last_seen_hours"])
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            devices = [
                d for d in devices if d.get("last_seen") is not None and d["last_seen"] >= cutoff
            ]

        # Return concise dicts (not full DB rows)
        return [_summarize_device(d) for d in devices]

    def _exec_fleet_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        """Aggregate fleet health stats."""
        health = self._get_registry().get_fleet_health()
        return health.model_dump()

    def _exec_active_alerts(self, params: dict[str, Any]) -> list[dict]:
        """Get active alerts with optional filters."""
        alerts = self.db.get_active_alerts()
        if "severity" in params:
            sev = str(params["severity"]).lower()
            alerts = [a for a in alerts if (a.get("severity") or "").lower() == sev]
        if "alert_type" in params:
            at = str(params["alert_type"]).lower()
            alerts = [a for a in alerts if (a.get("alert_type") or "").lower() == at]
        return alerts

    def _exec_device_telemetry(self, params: dict[str, Any]) -> list[dict]:
        """Get time-series telemetry for a device."""
        node_id = params.get("node_id", "")
        if not node_id:
            return []
        hours = int(params.get("hours", 24))
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.db.get_telemetry_history(node_id, since=since)

        metric = str(params.get("metric", "")).lower()
        if metric and rows:
            # Filter to requested metric columns
            metric_map = {
                "battery": "battery_level",
                "rssi": "rssi",
                "snr": "snr",
                "voltage": "voltage",
            }
            col = metric_map.get(metric)
            if col:
                rows = [
                    {"timestamp": r["timestamp"], metric: r.get(col), "node_id": r["node_id"]}
                    for r in rows
                ]
        return rows

    def _exec_mesh_topology(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get mesh topology graph."""
        edges = self.db.get_all_edges()
        components = self._get_topology().find_connected_components()
        isolated = self._get_topology().get_isolated_nodes()
        return {
            "edges": edges,
            "components": [[str(n) for n in c] for c in components],
            "isolated_nodes": isolated,
            "total_edges": len(edges),
            "component_count": len(components),
        }

    def _exec_find_spof(self, params: dict[str, Any]) -> list[str]:
        """Find single points of failure."""
        return self._get_topology().find_single_points_of_failure()

    def _exec_device_history(self, params: dict[str, Any]) -> list[dict]:
        """Get alert history for a specific device."""
        node_id = params.get("node_id", "")
        if not node_id:
            return []
        hours = int(params.get("hours", 24))
        # Get all alerts for this node (resolved + active)
        with self.db.connection() as conn:
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            rows = conn.execute(
                """SELECT * FROM alerts WHERE node_id = ? AND created_at >= ?
                   ORDER BY created_at DESC""",
                (node_id, since),
            ).fetchall()
            return [dict(r) for r in rows]

    def _exec_offline_transitions(self, params: dict[str, Any]) -> list[dict]:
        """Find devices that went offline recently."""
        hours = int(params.get("hours", 24))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        devices = self.db.list_devices()

        now = datetime.now(timezone.utc)
        threshold = timedelta(seconds=600)
        offline_recent = [
            _summarize_device(d)
            for d in devices
            if _is_offline(d, now, threshold)
            and d.get("last_seen") is not None
            and d["last_seen"] >= cutoff
        ]
        return offline_recent

    # ── Keyword fallback ──────────────────────────────────────────────

    def _keyword_fallback(self, question: str) -> Optional[FleetQueryResponse]:
        """Match common question patterns to pre-built query plans."""
        q_lower = question.lower()

        best_match: Optional[tuple[QueryFunction, dict[str, Any]]] = None
        best_score = 0

        for keywords, func, params in _KEYWORD_PATTERNS:
            score = sum(1 for kw in keywords if kw in q_lower)
            if score > best_score:
                best_score = score
                best_match = (func, params)

        if best_match is None or best_score == 0:
            return None

        func, params = best_match
        plan = QueryPlan(
            steps=[QueryStep(function=func, params=params, description="keyword match")],
            reasoning=f"Keyword fallback (matched {best_score} keywords)",
        )
        raw_data = self._execute_plan(plan)
        answer = self._simple_format(question, raw_data)

        return FleetQueryResponse(
            question=question,
            answer=answer,
            source="keyword",
            query_plan=plan,
            raw_data=raw_data,
        )

    # ── Canned fallback ───────────────────────────────────────────────

    def _canned_response(self, question: str) -> FleetQueryResponse:
        """Return a helpful message pointing to canned queries."""
        suggestions = "\n".join(f"  - {q['question']}: {q['description']}" for q in CANNED_QUERIES)
        answer = f"I couldn't understand that question. Try one of these:\n{suggestions}"
        return FleetQueryResponse(
            question=question,
            answer=answer,
            source="canned",
        )

    # ── Formatting ────────────────────────────────────────────────────

    def _simple_format(self, question: str, raw_data: dict[str, Any]) -> str:
        """Build a plain-text answer from raw results (no LLM needed)."""
        parts: list[str] = []
        for key, value in raw_data.items():
            if isinstance(value, list):
                if len(value) == 0:
                    parts.append("No results found.")
                else:
                    parts.append(f"Found {len(value)} result(s):")
                    for item in value[:10]:  # Cap display at 10
                        if isinstance(item, dict):
                            name = item.get("name") or item.get("node_id") or str(item)
                            parts.append(f"  - {name}")
                        else:
                            parts.append(f"  - {item}")
                    if len(value) > 10:
                        parts.append(f"  ... and {len(value) - 10} more")
            elif isinstance(value, dict):
                if "error" in value:
                    parts.append(f"Error: {value['error']}")
                else:
                    # Fleet summary or topology summary
                    for k, v in value.items():
                        if k.startswith("_"):
                            continue
                        parts.append(f"  {k}: {v}")
        return "\n".join(parts) if parts else "No data available."

    # ── Logging ───────────────────────────────────────────────────────

    def _log_query(self, question: str, result: FleetQueryResponse) -> None:
        """Persist query to nl_query_log table."""
        try:
            plan_json = None
            if result.query_plan is not None:
                plan_json = result.query_plan.model_dump_json()
            summary = result.answer[:500] if result.answer else None
            self.db.log_nl_query(
                question,
                query_plan_json=plan_json,
                result_summary=summary,
                source=result.source,
                duration_ms=result.duration_ms,
                ollama_available=result.ollama_available,
            )
        except Exception:
            logger.exception("Failed to log NL query")


# ── Module-level helpers ──────────────────────────────────────────────


def _version_lt(a: str, b: str) -> bool:
    """Compare semantic versions: return True if a < b."""
    try:
        a_parts = [int(x) for x in re.split(r"[.\-+]", a) if x.isdigit()]
        b_parts = [int(x) for x in re.split(r"[.\-+]", b) if x.isdigit()]
        if not a_parts or not b_parts:
            return False
        # Pad to same length
        max_len = max(len(a_parts), len(b_parts))
        a_parts.extend([0] * (max_len - len(a_parts)))
        b_parts.extend([0] * (max_len - len(b_parts)))
        return a_parts < b_parts
    except (ValueError, TypeError):
        return False


def _is_offline(device: dict, now: datetime, threshold: timedelta) -> bool:
    """Check if a device is offline based on last_seen."""
    last_seen = device.get("last_seen")
    if last_seen is None:
        return True
    try:
        if isinstance(last_seen, str):
            ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
        else:
            ls = last_seen
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
        return (now - ls) > threshold
    except (ValueError, TypeError):
        return True


def _summarize_device(d: dict) -> dict:
    """Extract key fields from a raw device row for LLM consumption."""
    return {
        "node_id": d.get("node_id", ""),
        "name": d.get("long_name") or d.get("short_name") or d.get("node_id", ""),
        "role": d.get("role", ""),
        "firmware": d.get("firmware_version", ""),
        "battery": d.get("battery_level"),
        "rssi": d.get("signal_rssi"),
        "snr": d.get("signal_snr"),
        "last_seen": d.get("last_seen"),
        "mesh_status": d.get("mesh_status", "unknown"),
        "latitude": d.get("latitude"),
        "longitude": d.get("longitude"),
    }
