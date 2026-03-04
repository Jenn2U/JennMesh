# JennMesh — CLAUDE.md

## Project Overview

JennMesh is the centralized Meshtastic LoRa radio fleet management service for the JENN Intelligent Ecosystem. It handles initial radio provisioning, firmware tracking, channel/security configuration, MQTT relay setup, fleet health monitoring, and lost node location.

**Version**: 0.6.0
**Language**: Python 3.11+
**Type**: Standalone mesh management service with web dashboard + agent daemon + CLI tools
**Tests**: 1536 (pytest) — target 80%+

## Architecture

JennMesh is a standalone service following JennSentry's proven pattern. It does NOT depend on JennEdge, Jenn Production, or any other JENN project at runtime.

### Components
- **jenn-mesh[agent]** — Lightweight daemon on edge nodes, talks to local radio via serial/TCP
- **jenn-mesh[dashboard]** — FastAPI web UI at mesh.jenn2u.ai (port 8002)
- **jenn-mesh[cli]** — Bench provisioning tools for USB radio setup
- **Dedicated MQTT broker** — Mosquitto on port 1884, isolated from Production's broker

### Hub-and-Spoke Independence
- Radios uplink telemetry to dedicated MQTT broker (NOT Production's Mosquitto)
- Dashboard subscribes to dedicated broker for fleet visibility
- Agent runs alongside JennEdge but operates independently
- No API calls to Jenn Production required for core functionality

## Cross-Project Dependencies (CRITICAL)

JennMesh manages radios but does NOT depend on these projects at runtime:
- **Jenn Production** (`/Users/mags/Jenn`) — No runtime dependency
- **JennEdge** (`/Users/mags/JennEdge`) — Agent installs alongside but is independent
- **JennSentry** (`/Users/mags/JennSentry`) — Sentry agent monitors JennMesh (not the reverse)

**Contract**: See `/Users/mags/Jenn/docs/CROSS_PROJECT_CONTRACT.md`

## Key Files

| File | Purpose |
|------|---------|
| `src/jenn_mesh/models/device.py` | MeshDevice, DeviceRole, FirmwareInfo, ConfigHash |
| `src/jenn_mesh/models/channel.py` | ChannelConfig, PSKManager |
| `src/jenn_mesh/models/fleet.py` | FleetHealth, NodeStatus, Alert, AlertType, INTERNET_DOWN |
| `src/jenn_mesh/models/heartbeat.py` | MeshHeartbeat, ServiceStatus, HeartbeatSummary |
| `src/jenn_mesh/models/emergency.py` | EmergencyType, BroadcastStatus, EmergencyBroadcast, mesh text format |
| `src/jenn_mesh/models/recovery.py` | RecoveryCommandType/Status, wire format helpers, ALLOWED_COMMANDS/SERVICES |
| `src/jenn_mesh/models/location.py` | GPSPosition, LostNodeQuery, ProximityResult |
| `src/jenn_mesh/core/registry.py` | SQLite WAL device registry |
| `src/jenn_mesh/core/config_manager.py` | Golden config CRUD, drift detection |
| `src/jenn_mesh/core/channel_manager.py` | PSK generation, channel distribution |
| `src/jenn_mesh/core/mqtt_subscriber.py` | Subscribes to mesh broker, ingests telemetry + heartbeats |
| `src/jenn_mesh/core/heartbeat_receiver.py` | Parses HEARTBEAT\| text messages, stores in DB, stale detection |
| `src/jenn_mesh/core/emergency_manager.py` | EmergencyBroadcastManager — validate, store, MQTT command, delivery confirmation |
| `src/jenn_mesh/core/recovery_manager.py` | RecoveryManager — validate, DB store, MQTT publish, rate limit, status tracking |
| `src/jenn_mesh/agent/radio_bridge.py` | Serial/TCP connection to local Meshtastic radio |
| `src/jenn_mesh/agent/remote_admin.py` | PKC remote admin commands via mesh |
| `src/jenn_mesh/agent/heartbeat_sender.py` | Builds + sends periodic heartbeat text messages over LoRa |
| `src/jenn_mesh/agent/recovery_handler.py` | Target-agent-side: validate nonce/timestamp, execute OS commands, send ACK |
| `src/jenn_mesh/agent/recovery_relay.py` | Gateway-agent-side: MQTT → mesh relay, forward RECOVER_ACK back to MQTT |
| `src/jenn_mesh/provisioning/bench_flash.py` | USB detect + golden config flash |
| `src/jenn_mesh/provisioning/security.py` | PKC admin key gen, Managed Mode setup |
| `src/jenn_mesh/provisioning/firmware.py` | Firmware version tracking, update flagging |
| `src/jenn_mesh/locator/tracker.py` | GPS position aggregation from mesh |
| `src/jenn_mesh/locator/finder.py` | Lost node locator (last known + proximity) |
| `src/jenn_mesh/core/workbench_manager.py` | Single-radio workbench session (connect/read/edit/apply/save) |
| `src/jenn_mesh/models/config_queue.py` | ConfigQueueStatus enum, ConfigQueueEntry model, backoff constants |
| `src/jenn_mesh/core/config_queue_manager.py` | ConfigQueueManager: enqueue, retry loop, backoff, alert escalation |
| `src/jenn_mesh/core/bulk_push.py` | Bulk push golden templates to fleet via RemoteAdmin (auto-enqueues failures) |
| `src/jenn_mesh/models/workbench.py` | Pydantic models for workbench + bulk push |
| `src/jenn_mesh/dashboard/routes/workbench.py` | 9 API endpoints (workbench + bulk push) |
| `src/jenn_mesh/dashboard/routes/heartbeat.py` | 3 API endpoints (per-device, recent, fleet mesh-status) |
| `src/jenn_mesh/dashboard/routes/emergency.py` | 4 API endpoints (send broadcast, list, get, fleet status) |
| `src/jenn_mesh/dashboard/routes/recovery.py` | 4 API endpoints (send command, list history, get by ID, node status) |
| `src/jenn_mesh/dashboard/routes/config_queue.py` | 5 API endpoints (list, get, retry, cancel, device status) |
| `src/jenn_mesh/core/drift_remediation.py` | DriftRemediationManager: preview, remediate, remediate-all, status |
| `src/jenn_mesh/core/failover_manager.py` | FailoverManager: assess, execute, revert, cancel, check_recoveries |
| `src/jenn_mesh/models/failover.py` | FailoverEvent, FailoverCompensation, ImpactAssessment models + enums |
| `src/jenn_mesh/dashboard/routes/failover.py` | 7 API endpoints (assess, execute, revert, cancel, status, active, check-recoveries) |
| `src/jenn_mesh/core/mesh_watchdog.py` | MeshWatchdog: 10-check periodic health monitor with auto-resolve + audit trail |
| `src/jenn_mesh/dashboard/routes/watchdog.py` | 3 API endpoints (status, history, trigger) |
| `src/jenn_mesh/core/sync_relay_manager.py` | SyncRelayManager: gateway CRDT relay (Production API ↔ LoRa mesh) |
| `src/jenn_mesh/core/sync_fragmenter.py` | SyncFragmenter/SyncReassembler: LoRa payload fragmentation with CRC-16 |
| `src/jenn_mesh/models/sync_relay.py` | Wire protocol (6 types), enums, format/parse helpers, SV hash, CRC-16 |
| `src/jenn_mesh/dashboard/routes/sync_relay.py` | 5 API endpoints (status, sessions, session detail, log, trigger) |
| `src/jenn_mesh/dashboard/app.py` | FastAPI dashboard application factory |
| `src/jenn_mesh/dashboard/middleware.py` | Security headers, request logging, rate limiting, CORS |
| `src/jenn_mesh/dashboard/error_handlers.py` | Global exception handlers (HTTP, validation, unhandled) |
| `src/jenn_mesh/dashboard/lifespan.py` | Application startup/shutdown lifecycle |
| `src/jenn_mesh/dashboard/logging_config.py` | Rotating file + console logging configuration |
| `src/jenn_mesh/cli.py` | CLI entry point with subcommands |
| `src/jenn_mesh/core/config_rollback.py` | OTA config rollback: snapshot → monitor → auto-rollback |
| `src/jenn_mesh/dashboard/routes/config_rollback.py` | 4 API endpoints (snapshots, snapshot detail, manual rollback, status) |
| `src/jenn_mesh/inference/ollama_client.py` | Async Ollama wrapper (chat, structured output, 4 feature methods) |
| `src/jenn_mesh/core/anomaly_detector.py` | Ollama anomaly detection + deterministic fallback |
| `src/jenn_mesh/core/alert_summarizer.py` | Ollama alert summarization (fleet + per-node) |
| `src/jenn_mesh/core/geofencing.py` | GeofencingManager — circle/polygon fences, breach detection |
| `src/jenn_mesh/core/coverage_mapper.py` | CoverageMapper — RSSI heatmap grid, dead zones, GeoJSON export |
| `src/jenn_mesh/core/fleet_analytics.py` | FleetAnalytics — trends, message volume, battery health |
| `src/jenn_mesh/models/geofence.py` | GeoFence, GeoFenceEvent, GeoFenceCheck models |
| `src/jenn_mesh/models/coverage.py` | CoverageSample, CoverageGrid, CoverageHeatmap models |
| `src/jenn_mesh/dashboard/routes/geofencing.py` | 6 API endpoints (CRUD + breaches) |
| `src/jenn_mesh/dashboard/routes/anomaly.py` | 4 API endpoints (node, fleet, history, status) |
| `src/jenn_mesh/dashboard/routes/alert_summary.py` | 3 API endpoints (fleet summary, per-node, status) |
| `src/jenn_mesh/dashboard/routes/coverage.py` | 4 API endpoints (heatmap, dead-zones, stats, export) |
| `src/jenn_mesh/dashboard/routes/analytics.py` | 5 API endpoints (uptime, battery, alerts, messages, summary) |
| `src/jenn_mesh/core/provisioning_advisor.py` | Ollama deployment advisor + deterministic fallback |
| `src/jenn_mesh/core/lost_node_reasoner.py` | Ollama lost node reasoning (GPS, battery, topology context) |
| `src/jenn_mesh/core/env_telemetry.py` | EnvTelemetryManager — threshold alerts, fleet summary |
| `src/jenn_mesh/models/env_telemetry.py` | EnvReading, EnvThreshold, EnvAlert models |
| `src/jenn_mesh/dashboard/routes/provisioning_advisor.py` | 2 API endpoints (recommend, status) |
| `src/jenn_mesh/dashboard/routes/lost_node_ai.py` | 2 API endpoints (ai-reasoning, status) |
| `src/jenn_mesh/dashboard/routes/env_telemetry.py` | 5 API endpoints (node history, fleet summary, thresholds, alerts) |
| `src/jenn_mesh/models/api.py` | Shared API response models (PaginatedResponse, StatusResponse, ConfirmRequest) |
| `src/jenn_mesh/models/encryption.py` | EncryptionStatus enum, DeviceEncryptionAudit, FleetEncryptionReport |
| `src/jenn_mesh/models/webhook.py` | WebhookEventType, WebhookConfig, WebhookDeliveryStatus, WebhookPayload |
| `src/jenn_mesh/models/notification.py` | NotificationChannelType, SlackConfig, TeamsConfig, EmailConfig, NotificationChannel, NotificationRule |
| `src/jenn_mesh/models/partition.py` | PartitionEventType, PartitionEvent, PartitionStatus |
| `src/jenn_mesh/models/bulk_ops.py` | BulkOperationType, BulkOperationStatus, TargetFilter, BulkOperationRequest, BulkOperationProgress |
| `src/jenn_mesh/core/encryption_auditor.py` | EncryptionAuditor: PSK strength, channel audit, fleet encryption score |
| `src/jenn_mesh/core/webhook_manager.py` | WebhookManager: CRUD, HMAC-SHA256 signing, dispatch, retry engine |
| `src/jenn_mesh/core/notification_dispatcher.py` | NotificationDispatcher: multi-channel alert routing (Slack, Teams, Email) |
| `src/jenn_mesh/core/partition_detector.py` | PartitionDetector: graph component analysis, relay placement recommendations |
| `src/jenn_mesh/core/bulk_operation_manager.py` | BulkOperationManager: preview, execute, cancel, progress tracking |
| `src/jenn_mesh/dashboard/routes/encryption.py` | 3 API endpoints (fleet audit, device audit, fleet score) |
| `src/jenn_mesh/dashboard/routes/webhooks.py` | 7 API endpoints (CRUD + test fire + deliveries) |
| `src/jenn_mesh/dashboard/routes/notifications.py` | 9 API endpoints (channel CRUD + test + rule CRUD) |
| `src/jenn_mesh/dashboard/routes/partitions.py` | 3 API endpoints (status, events, event detail) |
| `src/jenn_mesh/dashboard/routes/bulk_ops.py` | 5 API endpoints (preview, execute, progress, cancel, list) |
| `src/jenn_mesh/db.py` | SQLite WAL schema v14 (31 tables, ~134 DB methods) |
| `configs/*.yaml` | Golden Meshtastic config templates per device role |
| `deploy/systemd/*.service` | 4 systemd unit files (broker, dashboard, agent, sentry) |
| `deploy/scripts/install.sh` | 9-phase idempotent installer for ARM64 Linux |
| `deploy/scripts/package-release.sh` | Build release tarball for deployment |
| `deploy/scripts/health-check.sh` | Post-deploy health verification |
| `deploy/config/env.template` | Environment variables template |
| `deploy/config/mosquitto-prod.conf` | Production Mosquitto configuration |
| `deploy/udev/99-meshtastic.rules` | Stable /dev/meshtastic* USB symlinks |

## Golden Config Templates

4 role-based YAML templates in `configs/`:
- `relay-node.yaml` — ROUTER role, GPS off, MQTT relay enabled
- `edge-gateway.yaml` — CLIENT_MUTE role, MQTT relay, wifi config
- `mobile-client.yaml` — CLIENT role, GPS on, BLE on
- `sensor-node.yaml` — SENSOR role, environment module on

All templates include: PKC admin key, encrypted channels, MQTT pointing to dedicated broker with TLS.

## Dashboard

FastAPI at `mesh.jenn2u.ai` (port 8002, behind Azure Front Door):

**7 Pages**: Fleet Map | Device List | Config Manager | Provisioning | Lost Node Locator | Alerts | Radio Workbench

- **Run**: `jenn-mesh serve --port 8002`
- **Stack**: FastAPI + Jinja2 + vanilla JS (no build step)
- **Design**: Thinking Canvas — teal `#0D7377`, amber `#D97706`, DM Sans/Inter/JetBrains Mono

## Production Hardening (MESH-047)

The dashboard uses a layered middleware + error handling stack:

### Middleware Stack (raw ASGI, outermost first)
1. `CORSMiddleware` — allows localhost, LAN (10.x, 192.168.x, 172.16-31.x), mesh.jenn2u.ai
2. `RateLimitMiddleware` — per-IP sliding-window deque, 120 req/min, skips `/health`
3. `RequestLoggingMiddleware` — logs method/path/status/duration_ms, skips `/health` + `/static`
4. `SecurityHeadersMiddleware` — X-Content-Type-Options, X-Frame-Options, X-XSS-Protection
5. `_NoCacheAPIMiddleware` — Cache-Control: no-store for `/api/` routes (Front Door)

### Error Handling Pattern
- **All errors use `raise HTTPException(status_code, detail=...)`** — never `return {"error": ...}`
- Global handlers in `error_handlers.py` → `register_error_handlers(app)`
- HTTPException → JSON `{"detail": ..., "status_code": ...}`; 4xx logged as warning, 5xx as error
- RequestValidationError → 422 with structured field errors
- Unhandled Exception → 500 with `logger.exception()`, generic response

### Lifespan Management
- `@asynccontextmanager` in `lifespan.py` — startup: logging, DB, ConfigQueueManager, ConfigRollbackManager, WorkbenchManager, BulkPushManager (wired to config queue + rollback), EmergencyBroadcastManager, RecoveryManager, DriftRemediationManager (wired to config queue + rollback), FailoverManager, MeshWatchdog, WebhookManager, NotificationDispatcher (wired to webhook_manager), PartitionDetector, BulkOperationManager, EncryptionAuditor, config queue retry loop, watchdog loop, webhook delivery loop, startup_time
- Graceful degradation: if DB init fails, dashboard runs degraded (health reports "degraded")
- Test DB injection: `create_app(db=test_db)` sets state directly (httpx ASGITransport doesn't fire lifespan)

### Health Endpoint (`/health`)
- Components (17): database (schema_version), workbench, bulk_push, mesh_heartbeats, emergency_broadcasts, recovery_commands, config_queue, drift_remediation, failover, mesh_watchdog, config_rollback, sync_relay, encryption_audit, webhooks, notifications, partition_detection, bulk_operations, uptime_seconds
- Overall status: "healthy" or "degraded" (if any component fails)

### Logging
- Rotating file handler: `/var/log/jenn-mesh/dashboard.log` (10MB × 5 backups, fallback to `./logs/`)
- Console handler to stderr; plain text format (not JSON)
- `configure_logging()` called during lifespan startup

## Mesh Heartbeat Subsystem (MESH-031)

Edge nodes send periodic heartbeat text messages over LoRa radio so JennMesh can differentiate "internet down but alive" from "truly dead" nodes.

### Wire Protocol
`HEARTBEAT|{nodeId}|{uptime_s}|{services}|{battery}|{timestamp}[|{sv_hash}]`
- ~60-80 bytes (well under LoRa 256-byte limit), optional 8-char SV hash for CRDT sync
- Interval: 120 seconds (configurable via `--heartbeat-interval`)
- Services: comma-separated `name:status` pairs (e.g., `edge:ok,mqtt:down`)

### Sync Relay Wire Protocol (MESH-027)
6 pipe-delimited message types on Channel 1 (ADMIN), max 200 bytes usable:
- `SYNC_SV|{node_id}|{sv_json}` — full state vector
- `SYNC_REQ|{session_id}|{total_frags}|{priority}` — announce incoming delta
- `SYNC_FRAG|{session_id}|{seq}|{total}|{crc16}|{b64_payload}` — fragment
- `SYNC_ACK|{session_id}|{seq}` / `SYNC_NACK|{session_id}|{seq}` — ACK/NACK
- `SYNC_META|{node_id}|{key}|{value}` — single metadata update

### Database (Schema v4 → v5 adds emergency_broadcasts, v5 → v6 adds recovery_commands, v6 → v7 adds config_queue, v7 → v8 adds failover_events + failover_compensations, v8 → v9 adds watchdog_runs, v9 → v10 adds config_snapshots, v10 → v11 adds crdt_sync_queue + crdt_sync_fragments + crdt_sync_log, v13 → v14 adds webhooks + webhook_deliveries + notification_channels + notification_rules + partition_events + bulk_operations)
- `mesh_heartbeats` table — stores every received heartbeat
- `devices.mesh_status` — `"reachable"` | `"unreachable"` | `"unknown"`
- `devices.last_mesh_heartbeat` — ISO timestamp of last heartbeat
- Migration v3→v4 is idempotent (try/except on ALTER TABLE)

### Alert Differentiation
- **Node offline + no mesh heartbeat** → `NODE_OFFLINE` (critical)
- **Node offline + mesh heartbeat alive** → `INTERNET_DOWN` (warning)
- `DeviceRegistry.check_offline_nodes()` checks `mesh_status` before choosing alert type

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/heartbeat/{node_id}` | Latest heartbeat + history |
| `GET` | `/api/v1/heartbeat/recent/all?minutes=10` | All recent heartbeats |
| `GET` | `/api/v1/fleet/mesh-status` | Fleet mesh reachability grouping |

### Route Order Gotcha
The heartbeat router must be registered **before** the fleet router in `app.py` because `/fleet/mesh-status` would otherwise match `/fleet/{node_id}` (FastAPI matches by registration order).

## Emergency Broadcast System (MESH-026)

Operators push critical alerts to all field radios over LoRa mesh when internet/cloud is down.

### Architecture: Dashboard → MQTT → Agent → Mesh
1. Dashboard API receives broadcast → validates → stores in DB → publishes JSON to MQTT command topic
2. Agent subscribes to `jenn/mesh/command/emergency` → sends text via `RadioBridge.send_text(text, channel_index=3)`
3. MQTT subscriber detects `[EMERGENCY:` prefix in mesh-relayed text → updates broadcast status to `delivered`

### Wire Format
`[EMERGENCY:{TYPE}] {message}` — human-readable on radio screens, machine-parseable by MQTT subscriber.

### Emergency Types
`evacuation`, `network_down`, `severe_weather`, `security_alert`, `all_clear`, `custom`

### Broadcast Statuses
`pending` → `sending` → `sent` → `delivered` | `failed`

### Database (Schema v5)
- `emergency_broadcasts` table — stores every broadcast with status tracking
- `idx_emergency_status` index on `(status, created_at DESC)`
- 5 DB methods: `create_emergency_broadcast`, `update_broadcast_status`, `get_broadcast`, `list_broadcasts`, `get_recent_broadcasts`

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/emergency/broadcast` | Send broadcast (requires `confirmed: true`) |
| `GET` | `/api/v1/emergency/broadcasts` | List broadcast history |
| `GET` | `/api/v1/emergency/broadcast/{id}` | Get specific broadcast |
| `GET` | `/api/v1/emergency/status` | Fleet emergency status |

### Safety
- `confirmed: true` required on POST — returns 400 if missing or false (irreversible action)
- Channel 3 (Emergency) — already configured on all devices via golden config templates
- All radios display emergency messages on their screens

## Edge Node Recovery System (MESH-025)

Send recovery commands to offline edge nodes via LoRa mesh — the killer feature for remote fleet management when internet is down.

### Architecture: Dashboard → MQTT → Gateway Agent → Mesh → Target Agent
1. Dashboard API receives command → validates → stores in DB → publishes JSON to `jenn/mesh/command/recovery`
2. Gateway agent (RecoveryRelay) subscribes MQTT → sends text via `RadioBridge.send_text(destination=target, channel_index=1)`
3. Target agent (RecoveryHandler) receives mesh text → validates nonce+timestamp → executes OS command → sends `RECOVER_ACK` back
4. MQTT subscriber detects `RECOVER_ACK|` prefix → updates command status in DB

### Wire Protocol
- Command: `RECOVER|{cmd_id}|{command_type}|{args}|{nonce}|{timestamp}` (~60-100 bytes)
- ACK: `RECOVER_ACK|{cmd_id}|{status}|{message}`
- Channel 1 (ADMIN) with PSK encryption — all fleet devices share the ADMIN PSK via golden config

### Allowed Commands (hardcoded frozenset — NOT configurable)
| Command | OS Action |
|---------|-----------|
| `reboot` | `sudo shutdown -r now` |
| `restart_service` | `sudo systemctl restart {service}` (validated against ALLOWED_SERVICES) |
| `restart_ollama` | `sudo systemctl restart ollama` |
| `system_status` | Collects uptime, disk, memory, service states (read-only) |

### ALLOWED_SERVICES
`jennedge`, `jenn-sentry-agent`, `jenn-mesh-agent`, `ollama`

### Safety
- `confirmed: true` required on POST — returns 400 if missing or false
- Nonce (8-char hex) + Unix timestamp replay prevention (5-min tolerance)
- Bounded nonce deque (maxlen=100) on target agent
- Rate limit: 1 command per target node per 30 seconds (dashboard-side)
- Command expiry: `expires_at` = created_at + 5 minutes

### Database (Schema v6)
- `recovery_commands` table — stores every command with status tracking
- Statuses: `pending` → `sending` → `sent` → `completed` | `failed` | `expired`
- 6 DB methods: create, update_status, get, get_by_nonce, list, get_recent

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/recovery/send` | Send recovery command (requires `confirmed: true`) |
| `GET` | `/api/v1/recovery/commands` | List recovery command history |
| `GET` | `/api/v1/recovery/command/{command_id}` | Get specific command status |
| `GET` | `/api/v1/recovery/status/{node_id}` | Node recovery status summary |

### Agent CLI Flags
- `--recovery-disable` — Disables RecoveryHandler on target agents
- `--recovery-relay` — Enables RecoveryRelay on gateway agents
- An agent can run both handler and relay simultaneously

### MQTT Topics
- `jenn/mesh/command/recovery` — Dashboard → Gateway agent (JSON command payload)
- `jenn/mesh/command/recovery/ack` — Gateway agent → Dashboard (relay ACK after mesh send)

## Store-and-Forward Config Queue (MESH-028)

When `BulkPushManager` fails to deliver a config to an offline radio, it auto-enqueues the failed push into a persistent `config_queue` table. A background retry loop with exponential backoff attempts redelivery.

### Architecture: BulkPush failure → ConfigQueueManager → RemoteAdmin retry
1. `BulkPushManager._execute_push()` detects failure → calls `config_queue.enqueue()`
2. Background `asyncio` task calls `process_pending()` every 30 seconds
3. For each due entry: `RemoteAdmin.apply_remote_config()` via temp YAML file
4. Success → mark `delivered`; Failure → increment `retry_count`, compute exponential backoff
5. Max retries (10) exceeded → `failed_permanent` + `CONFIG_PUSH_FAILED` fleet alert

### Backoff Schedule
`compute_next_retry_delay(retry_count)` = `min(60 × 2^retry_count, 1920)`
→ 1m, 2m, 4m, 8m, 16m, 32m (cap)

### Queue Statuses
`pending` → `retrying` → `delivered` | `failed_permanent` | `cancelled`

### Database (Schema v7)
- `config_queue` table — stores YAML snapshot, retry state, delivery tracking
- Indexes on `(status, next_retry_at)` and `(target_node_id, created_at)`
- 7 DB methods: create, update_status, get, list, get_pending, get_stats, cancel

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/config-queue/entries` | List queue entries (filters: node, status, limit) |
| `GET` | `/api/v1/config-queue/entry/{id}` | Get specific entry |
| `POST` | `/api/v1/config-queue/entry/{id}/retry` | Manual retry (requires `confirmed: true`) |
| `POST` | `/api/v1/config-queue/entry/{id}/cancel` | Cancel entry (requires `confirmed: true`) |
| `GET` | `/api/v1/config-queue/status/{node_id}` | Device queue status |

### Design Decisions
- **YAML snapshot in queue** — templates can change between enqueue and retry; queued version is the intended version
- **retry_count NOT reset on manual retry** — preserves full audit trail
- **Optional wiring** — `BulkPushManager(db, config_queue=None)` default; wired in lifespan
- **No new wire protocol** — config payloads exceed LoRa 256-byte limit; RemoteAdmin handles fragmentation at firmware level

## Config Drift Auto-Remediation (MESH-023)

One-click fix for config-drifted devices. DriftRemediationManager coordinates ConfigManager, RemoteAdmin, and ConfigQueueManager.

### Flow
1. Dashboard → `GET /drift/{id}/preview` → shows golden template YAML + hash comparison
2. Operator confirms → `POST /drift/{id}/remediate` → writes temp YAML → RemoteAdmin push over mesh
3. Success → update DB hashes, resolve CONFIG_DRIFT + CONFIG_PUSH_FAILED alerts, log provisioning
4. Failure → auto-enqueue in config_queue for store-and-forward retry with exponential backoff

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/config/drift/{node_id}/preview` | Remediation preview (template YAML, hashes) |
| `POST` | `/api/v1/config/drift/{node_id}/remediate` | Fix single device (requires `confirmed: true`) |
| `POST` | `/api/v1/config/drift/remediate-all` | Fix all drifted (requires `confirmed: true`) |
| `GET` | `/api/v1/config/drift/{node_id}/status` | Remediation status (drift, queue, alerts, log) |

### Design Decisions
- **No device config fetch** — we know it's drifted (hashes differ); future enhancement could add true YAML diff
- **Enqueue on failure** — failed pushes go straight into ConfigQueueManager for automatic retry
- **Resolve both alert types** — `_handle_success()` resolves CONFIG_DRIFT + CONFIG_PUSH_FAILED
- **Lightweight coordinator** — no background tasks, no async loops; synchronous calls
- **Route order** — `remediate-all` (static) defined BEFORE `{node_id}` (param) to prevent FastAPI path capture

## Automated Failover (MESH-029)

When a relay SPOF goes offline, FailoverManager assesses impact, identifies compensation nodes, applies config changes via RemoteAdmin, and auto-reverts when the failed node recovers.

### Flow
1. Dashboard → `GET /failover/{id}/assess` → impact assessment (dependent nodes, candidates, suggested compensations)
2. Operator confirms → `POST /failover/{id}/execute` → apply compensations via RemoteAdmin
3. Recovery check → `POST /failover/check-recoveries` → auto-revert when failed node comes back online
4. Manual revert → `POST /failover/{event_id}/revert` → restore original config values

### Compensation Types
- `hop_limit_increase` (lora.hop_limit, max 7) — cheapest, just allows more hops
- `tx_power_increase` (lora.tx_power, max 30 dBm) — moderate battery cost
- `role_change` (device.role → ROUTER_CLIENT) — heaviest, makes node route traffic

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/failover/{node_id}/assess` | Impact assessment (read-only) |
| `POST` | `/api/v1/failover/{node_id}/execute` | Execute failover (`confirmed: true`) |
| `POST` | `/api/v1/failover/{event_id}/revert` | Revert compensations (`confirmed: true`) |
| `POST` | `/api/v1/failover/{event_id}/cancel` | Cancel without reverting (`confirmed: true`) |
| `GET` | `/api/v1/failover/{node_id}/status` | Node failover status |
| `GET` | `/api/v1/failover/active` | List all active failovers |
| `POST` | `/api/v1/failover/check-recoveries` | Auto-revert recovered nodes |

### Database (Schema v9)
- `failover_events` — lifecycle tracking (active → reverted/cancelled/revert_failed)
- `failover_compensations` — individual config changes with original_value for clean revert
- 8 DB methods: create/get/list/update events, create/get/update compensations

### Design Decisions
- **set_remote_config() over apply_remote_config()** — single-key changes are faster over LoRa, more granular for tracking/reverting
- **Battery guard** — skip candidates with battery < 30%
- **No auto-detection loop** — `check_recoveries()` called explicitly; now also called by MeshWatchdog
- **Separate router** — failover is operationally distinct from topology viewing
- **Provisioning actions** — `failover_execute` and `failover_revert` follow existing audit trail convention

## MESH-030: Mesh Watchdog

Background asyncio task that periodically invokes 11 health checks on staggered intervals. No new detection logic — purely orchestration and auto-alert management.

### Checks (11 total)
| Check | Interval | Method | Auto-resolve |
|-------|----------|--------|--------------|
| offline_nodes | 2 min | DeviceRegistry.check_offline_nodes() | ✅ |
| stale_heartbeats | 2 min | HeartbeatReceiver.check_stale_heartbeats() | — |
| low_battery | 5 min | DeviceRegistry.check_low_battery() | ✅ |
| health_scoring | 5 min | HealthScorer.score_fleet() | — |
| config_drift | 10 min | ConfigManager.get_drift_report() | ✅ |
| topology_spof | 10 min | TopologyManager.find_single_points_of_failure() | — (informational) |
| failover_recovery | 5 min | FailoverManager.check_recoveries() | — (built-in) |
| baseline_deviation | 10 min | BaselineManager.check_fleet_deviations() | ✅ |
| post_push_failures | 2 min | ConfigRollbackManager.check_post_push_failures() | ✅ (auto-rollback) |
| encryption_audit | 10 min | EncryptionAuditor.audit_fleet() | ✅ |
| partition_detection | 5 min | PartitionDetector.check_partitions() | ✅ |

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/watchdog/status` | Current state (checks, intervals, cycle count) |
| `GET` | `/api/v1/watchdog/history` | Audit trail (filter by check_name, limit) |
| `POST` | `/api/v1/watchdog/trigger/{check_name}` | Manually trigger a specific check |

### Database (Schema v9)
- `watchdog_runs` — audit trail for every check execution (timing, results, errors)
- 3 DB methods: create_watchdog_run, complete_watchdog_run, get_recent_watchdog_runs

### Key Design
- **Single loop, staggered checks** — one `asyncio.create_task()` with 60s sleep; each check tracks its own `_last_run`
- **Auto-resolve** — resolves alerts when conditions clear (battery recovers, node comes online, drift fixed)
- **Env disable** — `MESH_WATCHDOG_ENABLED=false` to disable entirely
- **Check isolation** — failure in one check does not affect others

## MESH-040: OTA Config Rollback

Safety net for config pushes — snapshot device config before push, monitor for post-push failures, auto-rollback if node goes offline.

### Snapshot Lifecycle
`active → monitoring → confirmed | rolled_back | rollback_failed` or `active → push_failed | snapshot_failed`

### Integration Points
- **BulkPushManager** — `snapshot_before_push()` before each device push, `mark_push_completed/failed()` after
- **DriftRemediationManager** — same pattern in `remediate_device()`
- **MeshWatchdog** — `post_push_failures` check (2-min interval) calls `check_post_push_failures()`
- **FailoverManager** — excluded (has its own `original_value`/`revert_failover()` mechanism)

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/config-rollback/snapshots` | List recent snapshots (filter by node_id) |
| `GET` | `/api/v1/config-rollback/snapshot/{id}` | Get snapshot details |
| `POST` | `/api/v1/config-rollback/snapshot/{id}/rollback` | Manual rollback (requires `confirmed: true`) |
| `GET` | `/api/v1/config-rollback/status` | System summary (monitoring count, breakdowns) |

### Database (Schema v10)
- `config_snapshots` table — 12 columns (node_id, push_source, yaml_before, yaml_after, status, monitoring_until, etc.)
- 6 DB methods: create_config_snapshot, update_config_snapshot, get_config_snapshot, get_snapshots_for_node, get_monitoring_snapshots, get_recent_snapshots

### Key Design
- **Skip-if-recent**: Reuses snapshot if one exists from < 5 min ago for same node (avoids 30-120s mesh round-trip)
- **Grace period monitoring**: Waits `monitoring_minutes` (default 10) before evaluating — config pushes trigger radio reboot
- **Alert lifecycle**: TRIGGERED → COMPLETED or FAILED (3 new AlertType values)
- **Optional injection**: `rollback_manager` parameter follows same pattern as `config_queue`

## MESH-027: Mesh Relay for Edge Sync

Gateway nodes with internet relay CRDT sync between Jenn Production and offline edge devices via LoRa mesh. Not a full-bandwidth replacement — LoRa caps at ~256 bytes per message — but keeps state vectors current, propagates tombstones, and syncs metadata.

### Architecture
`Jenn Production ↔ (HTTP) ↔ Gateway SyncRelayManager ↔ (LoRa fragments) ↔ Edge Node`

### Priority System
| Priority | Data Type | LoRa? |
|----------|-----------|-------|
| P1 (Critical) | Tombstones, config LWW | Yes, immediate |
| P2 (Important) | Conversation metadata | Yes, batched |
| P3 (Normal) | Memories (LWW) | Yes, if bandwidth allows |
| P4 (Deferred) | Full content (`data` field) | TCP only |

### Integration Points
- **HeartbeatSender** — optional `sv_hash` piggyback (8-char SHA-256 of state vector)
- **MQTTSubscriber** — `SYNC_*` prefix routing to SyncRelayManager
- **MeshWatchdog** — `sync_health` check (5-min interval) monitors pending queue + stale sessions
- **Health endpoint** — `sync_relay` component (13th)

### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/sync-relay/status` | Summary: active sessions, queue depth |
| `GET` | `/api/v1/sync-relay/sessions` | List sessions (filter by node_id, status) |
| `GET` | `/api/v1/sync-relay/session/{id}` | Session detail with fragment status |
| `POST` | `/api/v1/sync-relay/trigger/{node_id}` | Manual sync trigger (requires `confirmed: true`) |
| `GET` | `/api/v1/sync-relay/log` | Sync audit log (filter by node_id, direction) |

### Database (Schema v11)
- `crdt_sync_queue` — pending/active sync sessions with priority and status
- `crdt_sync_fragments` — individual fragments with CRC-16, ACK tracking
- `crdt_sync_log` — audit trail (items synced, bytes, duration, SV hashes)
- 12 DB methods following config_snapshots CRUD pattern

### Key Design
- **Per-bucket fragment session IDs** — each priority bucket gets its own session_id to avoid UNIQUE constraint collisions
- **Cooldown-based triggering** — suppress re-triggering for N minutes after sync completes
- **Content stripping** — `data` fields removed for LoRa; metadata-only; backfill on TCP reconnect
- **CRC-16/CCITT** — per-fragment integrity; NACK + retransmit on mismatch (max 3 retries)
- **4 new AlertType values**: sync_relay_started, sync_relay_completed, sync_relay_failed, sync_sv_mismatch

## v0.4.0 — Intelligence & Analytics

### Ollama Integration (`src/jenn_mesh/inference/ollama_client.py`)
- **Optional dependency**: `ollama>=0.4.0` in `[ollama]` extra group
- **Shared client**: `OllamaClient` used by 4 features (anomaly, summarization, provisioning advisor, lost node reasoning)
- **Config**: `OLLAMA_HOST` (default `http://localhost:11434`), `OLLAMA_MODEL` (default `qwen3:4b`)
- **Graceful degradation**: All features fall back to deterministic logic when Ollama unavailable
- Methods: `chat()`, `chat_json()`, `analyze_anomaly()`, `summarize_alerts()`, `advise_provisioning()`, `reason_lost_node()`

### Anomaly Detection (MESH-017, `src/jenn_mesh/core/anomaly_detector.py`)
- Ollama-powered telemetry anomaly analysis using baseline deviations
- Fleet-wide anomaly scanning with `analyze_fleet()`
- 4 API endpoints: `GET /api/v1/anomaly/{node_id}`, `/anomaly/fleet`, `/anomaly/history`, `/anomaly/status`

### Alert Summarization (MESH-018, `src/jenn_mesh/core/alert_summarizer.py`)
- Ollama collapses active alerts into human-readable summaries
- Per-node and fleet-wide summaries with topology context
- 3 API endpoints: `GET /api/v1/alerts/summary`, `/alerts/summary/{node_id}`, `/alerts/summary/status`

### Geofencing (MESH-019, `src/jenn_mesh/core/geofencing.py`)
- Circle (Haversine) and polygon (ray-casting) fence types
- Trigger modes: entry, exit, or both
- Node-filtered fences (apply to specific nodes or all)
- 6 API endpoints: `POST/GET/PUT/DELETE /api/v1/geofences`, `GET /geofences/breaches`
- Schema v12: `geofences` table + 5 CRUD methods

### Topology Visualization (MESH-024)
- D3.js force-directed interactive graph at `/topology`
- Color coding: online (teal `#0D7377`), offline (red `#DC2626`), degraded (amber `#D97706`)
- Edge thickness proportional to SNR; SPOF pulsing red ring; click-to-inspect sidebar
- Uses existing `GET /api/v1/topology` API (no new endpoints needed)

### Coverage Mapping (MESH-034, `src/jenn_mesh/core/coverage_mapper.py`)
- Aggregates RSSI observations into heatmap grid cells (configurable resolution)
- Dead zone detection, GeoJSON export for external GIS tools
- Leaflet.js heatmap overlay on dashboard
- 4 API endpoints: `GET /api/v1/coverage/heatmap`, `/coverage/dead-zones`, `/coverage/stats`, `/coverage/export`
- Schema v12: `coverage_samples` table + 5 CRUD methods

### Fleet Analytics (MESH-035, `src/jenn_mesh/core/fleet_analytics.py`)
- Uptime trends, battery trends, alert frequency, message volume, fleet growth
- SVG sparklines (same pattern as JennSentry)
- 5 API endpoints: `GET /api/v1/analytics/uptime`, `/analytics/battery`, `/analytics/alerts`, `/analytics/messages`, `/analytics/summary`

### Schema v12 Additions
- `geofences` table (circle/polygon, center/radius/polygon_json, trigger_on, node_filter)
- `coverage_samples` table (from_node, to_node, lat, lon, rssi, snr)
- 10 new DB methods across both tables
- 6 new AlertType values: anomaly_detected, geofence_breach, geofence_dwell, coverage_gap, coverage_degraded, env_threshold_exceeded

## v0.5.0 — Provisioning, Lost Node AI, Env Telemetry

### Provisioning Advisor (MESH-032, `src/jenn_mesh/core/provisioning_advisor.py`)
- Ollama-powered deployment recommendations with deterministic fallback
- Input: deployment context (terrain, num_nodes, power_source)
- Output: recommended roles (~30% routers), power settings, channel config, deployment order, warnings
- Terrain-aware: urban (ShortFast), mountainous (VeryLongSlow), forest (MediumSlow), indoor (ShortTurbo)
- 2 API endpoints: `POST /api/v1/advisor/recommend`, `GET /api/v1/advisor/status`

### Lost Node Reasoning (MESH-033, `src/jenn_mesh/core/lost_node_reasoner.py`)
- Ollama-powered location reasoning with deterministic fallback
- Builds context from: GPS history, battery level, topology edges, time since contact, node role
- Compass-direction movement analysis from position history
- Confidence levels: high/medium/low based on data recency and availability
- 2 API endpoints: `GET /api/v1/locate/{node_id}/ai-reasoning`, `GET /api/v1/locate/ai/status`

### Environmental Telemetry (MESH-039, `src/jenn_mesh/core/env_telemetry.py`)
- Ingest Meshtastic environment sensors: temperature, humidity, pressure, air quality
- Configurable thresholds per metric → `ENV_THRESHOLD_EXCEEDED` fleet alerts
- Default thresholds: temp (-20°C to 60°C), humidity (0-100%), pressure (870-1084 hPa), air quality (max 300)
- Fleet-wide summary with per-node latest readings
- 5 API endpoints: `GET /api/v1/environment/{node_id}`, `/environment/fleet/summary`, `GET/PUT /environment/thresholds`, `GET /environment/alerts`
- Models: `EnvReading`, `EnvThreshold`, `EnvAlert` in `src/jenn_mesh/models/env_telemetry.py`

### Schema v13 Additions
- `env_telemetry` table (node_id, temperature, humidity, pressure, air_quality, timestamp)
- 5 new DB methods: `add_env_reading`, `get_env_readings`, `get_fleet_env_summary`, `get_env_alerts`, `prune_old_env_readings`

### Route Ordering Pattern (IMPORTANT)
For routes with path parameters (e.g., `/environment/{node_id}`), specific routes MUST be registered before parameterized routes to avoid FastAPI capturing literals like "thresholds" as node_id values. Example in `env_telemetry.py`: `/environment/fleet/summary` and `/environment/thresholds` are defined before `/environment/{node_id}`.

## v0.6.0 — Security, APIs & Notifications

### API Versioning & OpenAPI Spec (MESH-064)

The dashboard now serves a comprehensive OpenAPI spec with 15 tag groups:

```python
OPENAPI_TAGS = [
    {"name": "Fleet Management", ...},
    {"name": "Topology & Coverage", ...},
    {"name": "Configuration", ...},
    {"name": "Alerts & Monitoring", ...},
    {"name": "AI & Intelligence", ...},
    {"name": "Environmental", ...},
    {"name": "Geofencing", ...},
    {"name": "Emergency", ...},
    {"name": "Recovery", ...},
    {"name": "Webhooks", ...},
    {"name": "Notifications", ...},
    {"name": "Partitions", ...},
    {"name": "Bulk Operations", ...},
    {"name": "Admin", ...},
    {"name": "Health", ...},
]
```

- `GET /openapi.json` — full spec; `GET /docs` — Swagger UI; `GET /redoc` — ReDoc
- Shared response models in `src/jenn_mesh/models/api.py`: `PaginatedResponse`, `StatusResponse`, `ConfirmRequest`
- All routes use `/api/v1/` prefix consistently

### Mesh Message Encryption Audit (MESH-058, `src/jenn_mesh/core/encryption_auditor.py`)

Audits channel PSK strength across the fleet to detect weak/default encryption.

- **PSK classification**: `unencrypted` (empty/default LONGFAST), `weak` (AES-128 or short key), `strong` (AES-256/32-byte key)
- Fleet encryption score (0-100): weighted by channel importance (Primary channel weighted higher)
- `ENCRYPTION_WEAK` alert type (WARNING severity) auto-created/resolved by MeshWatchdog

#### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/encryption/audit` | Fleet-wide encryption audit |
| `GET` | `/api/v1/encryption/audit/{node_id}` | Per-device encryption audit |
| `GET` | `/api/v1/encryption/score` | Fleet encryption score (0-100) |

### External System Webhooks (MESH-044, `src/jenn_mesh/core/webhook_manager.py`)

HTTP POST notifications for fleet events with HMAC-SHA256 signing, exponential backoff retry, and delivery tracking.

#### Architecture
1. Alert fires → `WebhookManager.dispatch_event()` matches event type to subscribed webhooks
2. For each match → enqueue delivery in `webhook_deliveries` table
3. Background `webhook_delivery_loop_task` calls `process_pending_deliveries()` every 30s
4. Each delivery: sign payload (HMAC-SHA256), POST to webhook URL, track HTTP status
5. Failure → exponential backoff retry (30s → 60s → 120s → 240s → 480s → 960s)
6. After 5 failed attempts → mark as permanently failed

#### Event Types (10)
`node_online`, `node_offline`, `alert_created`, `alert_resolved`, `geofence_entry`, `geofence_exit`, `emergency_broadcast`, `partition_detected`, `bulk_op_completed`, `test`

#### Payload Format
```json
{
  "event_type": "alert_created",
  "source": "jenn-mesh",
  "timestamp": "2026-03-04T12:00:00Z",
  "data": { ... }
}
```

#### HMAC-SHA256 Signing
`X-JennMesh-Signature: sha256={hex_digest}` — computed from `HMAC(secret, body)` (GitHub/Stripe pattern).

#### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/webhooks` | Create webhook |
| `GET` | `/api/v1/webhooks` | List webhooks |
| `GET` | `/api/v1/webhooks/{id}` | Get webhook |
| `PUT` | `/api/v1/webhooks/{id}` | Update webhook |
| `DELETE` | `/api/v1/webhooks/{id}` | Delete webhook |
| `POST` | `/api/v1/webhooks/{id}/test` | Test fire |
| `GET` | `/api/v1/webhooks/{id}/deliveries` | List deliveries |

#### Database (Schema v14)
- `webhooks` — HTTP POST targets (name, url, secret, event_types, is_active)
- `webhook_deliveries` — delivery attempt log (webhook_id, event_type, payload_json, status, attempt_count, http_status, next_retry_at)
- 12 DB methods: create/get/list/update/delete webhook, create/update/get_pending/list deliveries, prune old deliveries

### Notification Channels (MESH-060, `src/jenn_mesh/core/notification_dispatcher.py`)

Multi-channel alert routing layer on top of the webhook delivery engine.

#### Architecture
1. Alert fires → `NotificationDispatcher.notify(alert_type, severity, data)`
2. Dispatcher queries `notification_rules` for matching `(alert_type, severity) → channel_ids[]`
3. For each matching channel → format per type (Slack Block Kit, Teams Adaptive Card, Email SMTP)
4. Delegate HTTP channels (Slack/Teams) to webhook delivery engine; SMTP via smtplib

#### Channel Types
- **Slack**: Block Kit with color-coded severity (critical=red, warning=amber, info=teal)
- **Teams**: Adaptive Card (Office 365 incoming webhook)
- **Email**: SMTP with HTML body
- **Webhook**: Raw JSON HTTP POST (delegates to WebhookManager)

#### Notification Rules
`notification_rules` maps `(alert_types[], severities[]) → channel_ids[]`. Empty `alert_types` = wildcard (match all). Empty `severities` = wildcard (match all).

#### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/notifications/channels` | Create channel |
| `GET` | `/api/v1/notifications/channels` | List channels |
| `GET` | `/api/v1/notifications/channels/{id}` | Get channel |
| `PUT` | `/api/v1/notifications/channels/{id}` | Update channel |
| `DELETE` | `/api/v1/notifications/channels/{id}` | Delete channel |
| `POST` | `/api/v1/notifications/channels/{id}/test` | Test fire |
| `POST` | `/api/v1/notifications/rules` | Create rule |
| `GET` | `/api/v1/notifications/rules` | List rules |
| `DELETE` | `/api/v1/notifications/rules/{id}` | Delete rule |

#### Database (Schema v14)
- `notification_channels` — channel configs (name, type, config_json, is_active)
- `notification_rules` — routing rules (name, alert_types, severities, channel_ids)
- 10 DB methods: create/get/list/update/delete channel, create/get/list/update/delete rule, get_channels_for_alert

### Mesh Network Partitioning Detection (MESH-055, `src/jenn_mesh/core/partition_detector.py`)

Detects when the mesh network splits into disconnected components, stores topology diffs, and recommends relay placement.

#### Architecture
1. MeshWatchdog calls `PartitionDetector.check_partitions()` every 5 minutes
2. Reuses `TopologyManager._find_connected_components()` graph algorithm
3. Compares component count with previous snapshot
4. If partitioned (>1 component): creates `NETWORK_PARTITION` alert (CRITICAL) + stores `partition_event`
5. If resolved (back to 1 component): creates `PARTITION_RESOLVED` alert (INFO) + resolves partition event
6. Optional relay placement recommendation using GPS centroids between disconnected components

#### Alert Types
- `NETWORK_PARTITION` (CRITICAL) — mesh has split into multiple disconnected components
- `PARTITION_RESOLVED` (INFO) — mesh connectivity restored to single component

#### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/partitions/status` | Current partition status |
| `GET` | `/api/v1/partitions/events` | List partition events (filter by event_type) |
| `GET` | `/api/v1/partitions/events/{id}` | Get partition event detail |

#### Database (Schema v14)
- `partition_events` — network split/merge history (event_type, component_count, components_json, relay_recommendation, resolved_at)
- 6 DB methods: create/get/list/resolve partition events, get_latest_partition_event

### Bulk Fleet Operations (MESH-059, `src/jenn_mesh/core/bulk_operation_manager.py`)

Batch operations across the fleet with preview (dry-run), confirmation gate, progress tracking, and cancel support.

#### Safety Gate
- **Preview** (`POST /api/v1/bulk-ops/preview`): dry-run showing target count + warnings (always safe)
- **Execute** (`POST /api/v1/bulk-ops/execute`): requires `dry_run: false, confirmed: true` — returns 400 otherwise
- Factory reset operations get an explicit warning in preview response

#### Operation Types
`reboot`, `config_push`, `psk_rotation`, `factory_reset`, `firmware_update`

#### Target Filtering
`TargetFilter` supports: `all_devices: true`, `role: "ROUTER"`, `node_ids: ["!abc"]`, `hardware_model: "tbeam"` — filters are AND-combined. Empty filter `{}` returns ALL devices.

#### API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/bulk-ops/preview` | Preview targets + warnings (dry-run) |
| `POST` | `/api/v1/bulk-ops/execute` | Execute operation (`confirmed: true` required) |
| `GET` | `/api/v1/bulk-ops/{op_id}` | Get operation progress |
| `POST` | `/api/v1/bulk-ops/{op_id}/cancel` | Cancel running operation |
| `GET` | `/api/v1/bulk-ops` | List operations (filter by status) |

#### Database (Schema v14)
- `bulk_operations` — batch operation tracking (operation_type, target_filter, target_node_ids, parameters, status, total_targets, completed_count, failed_count, result_json)
- 6 DB methods: create/get/list/update/cancel bulk operations

### Schema v14 Summary (6 New Tables, ~34 New Methods)
| Table | Purpose | Methods |
|-------|---------|---------|
| `webhooks` | HTTP POST targets | 5 CRUD |
| `webhook_deliveries` | Delivery log + retry | 7 |
| `notification_channels` | Slack/Teams/Email configs | 5 CRUD |
| `notification_rules` | Alert → channel routing | 5 CRUD + get_channels_for_alert |
| `partition_events` | Network split/merge history | 6 |
| `bulk_operations` | Batch fleet actions | 6 |

## CLI Commands

```
jenn-mesh provision             # Interactive bench provisioning
jenn-mesh provision --role relay --port /dev/ttyUSB0
jenn-mesh fleet list            # List all known devices
jenn-mesh fleet health          # Show fleet health summary
jenn-mesh config drift          # Check for config drift
jenn-mesh locate <nodeId>       # Query last known position
jenn-mesh serve                 # Start dashboard
jenn-mesh agent                 # Start agent daemon
```

## Testing Conventions

- Run `pytest tests/ -v --tb=short` before committing
- Use mock radio connections and temp databases for all tests — no real hardware
- Mock the `meshtastic` library in provisioning and agent tests
- Target 80%+ coverage
- Async tests use `pytest-asyncio` with `asyncio_mode = "auto"`

## Code Style

- **Black** formatter with 100-char line length
- **Flake8** with `.flake8` config
- **mypy** strict mode
- Pre-commit hooks enforce all three

## TODO Comment Policy (MANDATORY)

Every `TODO`, `FIXME`, or `HACK` comment left in source code **must** have a corresponding Azure DevOps work item in the JennMesh backlog. This ensures nothing is forgotten between sessions.

- **Before writing a TODO**: Create an ADO Task first, then reference the work item ID in the comment (e.g., `# TODO(#575): dedicated channel_utilization field`)
- **Found an existing TODO without an ADO item?** Create one immediately
- **Stale TODOs** (work already done): Remove the comment and close the ADO item if one exists
- **Audits**: When finishing a feature, grep for `TODO|FIXME|HACK|XXX` and verify each has a matching ADO work item

## Version Bump Rule (MANDATORY)

- **BREAKING** → MAJOR (change radio protocol, break config format)
- **New feature** → MINOR (new CLI command, new dashboard page, new alert type)
- **Bug fix/perf/refactor** → PATCH (fix bridge, improve detection, dependency update)

**Update checklist** (same commit):
1. `VERSION` — version string
2. `pyproject.toml` — `version` field
3. `src/jenn_mesh/__init__.py` — `__version__`
4. `jenn-contract.json` — `project.version`
5. `CHANGELOG.md` — new version entry
6. `CROSS_PROJECT_CONTRACT.md` Section 7 — version matrix row

## Auto-Commit Policy (MANDATORY)

Never leave uncommitted work. Orphaned changes between sessions cause lost work.

- **Commit after every logical unit of work** (feature, fix, refactor) — don't batch unrelated changes
- **Push after every commit** — local-only commits are still at risk
- **WIP commits are OK** — `git commit -m "WIP: <description>"` is better than uncommitted files
- **Start of session**: check `git status` for orphaned changes from prior sessions, commit them first
- **Before ending**: always commit + push before signaling completion

## Notification Channels

- **Slack is the primary notification channel** (Block Kit format via incoming webhook)
- Teams (Adaptive Card) is the secondary channel
- Telegram was **retired in Jenn v6.3.0** — do not add Telegram references
- Supported channels: in_app (SSE), webhook (HTTP POST), email (SMTP), Slack, Teams
- See CROSS_PROJECT_CONTRACT.md Section 23 for the canonical `NotificationChannel` enum

## Port Allocation

| Port | Service | Scope |
|------|---------|-------|
| 8002 | JennMesh Dashboard | Cloud + local |
| 1884 | Dedicated Mosquitto (mesh) | Edge + cloud |

## Physical Deployment (Mesh Appliance)

JennMesh deploys as a bare-metal "mesh appliance" on ARM64 Linux (Pi 5 / Orange Pi) for USB/Bluetooth radio administration.

### Directory Layout
```
/opt/jenn-mesh/current -> <version>/   # Active install (symlink)
/etc/jenn-mesh/                        # Configuration (env, mosquitto.conf)
/var/lib/jenn-mesh/                    # Data (mesh.db, mosquitto, backups)
/var/log/jenn-mesh/                    # Service logs
```

### 4 Systemd Services
| Service | Description |
|---------|-------------|
| `jenn-mesh-broker` | Mosquitto MQTT on port 1884 |
| `jenn-mesh-dashboard` | FastAPI + uvicorn on port 8002 |
| `jenn-mesh-agent` | Radio bridge (serial/USB) → MQTT forwarder |
| `jenn-sentry-agent` | Health monitoring sidecar |

### Deploy Commands
```
deploy/scripts/package-release.sh      # Build release tarball
deploy/scripts/install.sh              # 9-phase idempotent installer
deploy/scripts/health-check.sh         # Post-deploy verification
deploy/scripts/backup-mesh-db.sh       # SQLite nightly backup (cron)
```

### Deploy Pipeline
- `.azure-pipelines/templates/deploy-mesh-server.yml` — SSH deploy template
- `.azure-pipelines/deploy-meshbox-01.yml` — Per-node deploy trigger

## Infrastructure

- **Physical server**: ARM64 Linux mesh appliance (LAN-only access)
- **Azure Container App**: `jennmesh-{env}` (dev/staging/prod) — cloud option
- **Front Door**: `mesh.jenn2u.ai` → Container App origin
- **Key Vault**: Fleet admin PKC private key in `kv-magnivation-claude`
- **MQTT Broker**: Dedicated Mosquitto (physical server or Docker)
