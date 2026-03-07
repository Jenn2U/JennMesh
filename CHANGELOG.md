# Changelog

All notable changes to JennMesh will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] - 2026-03-07

### Added
- **Dashboard provisioning toast notifications**: Floating bottom-right toasts show real-time radio provisioning events (detected, flashing, configured, complete, failed)
- **Provision tab badge counter**: Amber badge on Provision nav link shows active provisioning operation count
- **`GET /api/v1/provision/recent` endpoint**: Returns last 5 minutes of provisioning events with active_count for badge/toast
- **Granular provisioning log entries**: radio_detected, erase_started, config_applied, provision_complete, provision_failed, edge_yield actions replace single completion log
- **JennEdge priority coordination**: Radio watcher yields to JennEdge on shared nodes via stdlib health check (`GET localhost:8080/mesh/status`); configurable via `JENN_RADIO_EDGE_PRIORITY` env var
- **Systemd ordering**: `After=jenn-edge.service` + 5-second startup grace period ensures JennEdge claims its radio first

### Changed
- `radio_watcher.WatcherConfig` — added `edge_priority` and `edge_health_url` fields
- Service unit — added `ExecStartPre=/bin/sleep 5` for startup grace period

## [0.8.0] - 2026-03-05

### Added
- **Qwen2.5-Coder code model support**: Dedicated code model for structured output tasks
- `generate_config_yaml()` — Meshtastic YAML configuration generation via code model
- `analyze_recovery_script()` — Shell script safety analysis before execution
- Code model availability reporting in `health_info()` endpoint
- `OLLAMA_CODE_MODEL` environment variable for code model configuration

## [0.7.0] - 2026-03-04

### Added

- **TAK Server Integration / CoT Gateway** (MESH-065): `TakGateway` translates mesh node positions to Cursor on Target (CoT) XML for ATAK/WinTAK interop; bidirectional parse/generate; HMAC callsign generation; 6 API endpoints (`/tak/*`); configurable server host/port/TLS/callsign prefix
- **Team Communication via Mesh** (MESH-043): `TeamCommsManager` sends structured text messages over LoRa mesh Channel 2; wire format `[TEAM:{channel}] message`; broadcast/team/direct channels; 220-char limit (LoRa MTU); delivery lifecycle (pending→sending→sent→delivered); MQTT integration; 5 API endpoints (`/team-comms/*`); safety gate requires `confirmed=True`
- **Mesh-Based Asset Tracking** (MESH-053): `AssetTracker` registers vehicles, equipment, personnel, drones, sensors with GPS trail enrichment (haversine distance, speed, bearing); automatic status updates (active/idle/out_of_range); 8 API endpoints (`/assets/*`); supports zone/team/project filtering
- **JennEdge Cross-Reference** (MESH-057): `EdgeAssociationManager` maps JennEdge devices ↔ mesh radios; combined status query ("edge offline but radio transmitting"); stale detection (radio not seen > 1hr); 8 API endpoints (`/edge-associations/*`)
- DB schema v15: 5 new tables (`team_messages`, `tak_config`, `tak_events`, `assets`, `edge_associations`) with ~30 CRUD methods
- 19 OpenAPI tag groups (was 15); 171 API routes (was 144)
- 152 new tests (1536 → 1688 total)

### Fixed

- DB parameter binding order in `create_team_message()` — message/recipient columns were swapped

## [0.6.0] - 2026-03-04

### Added

- **API Versioning & OpenAPI Spec** (MESH-064): 15 OpenAPI tag groups organizing 32 routers; Swagger UI at `/docs`, ReDoc at `/redoc`; production + local dev server metadata; shared API response models (`PaginatedResponse`, `StatusResponse`, `ConfirmRequest`)
- **Mesh Message Encryption Audit** (MESH-058): `EncryptionAuditor` audits fleet PSKs against weak/default values (LONGFAST `0x01`, empty PSKs); fleet encryption score (0-100); 3 API endpoints (`/encryption/*`); watchdog check #11 with `ENCRYPTION_WEAK` alerts + auto-resolve
- **External System Webhooks** (MESH-044): `WebhookManager` with HMAC-SHA256 signing, exponential backoff retry (30s to 16min, 5 attempts); async delivery loop; 7 API endpoints (`/webhooks/*`); test-fire endpoint verification; delivery history
- **Notification Channels** (MESH-060): `NotificationDispatcher` with Slack Block Kit, Teams Adaptive Card, Email formatters; 9 API endpoints (`/notifications/*`); notification rules map `(alert_type, severity) -> channel_ids[]`; channel CRUD + test-fire + rule CRUD
- **Mesh Network Partitioning Detection** (MESH-055): `PartitionDetector` reuses `TopologyManager.find_connected_components()` graph algorithm; GPS centroid relay placement recommendations; 3 API endpoints (`/partitions/*`); watchdog check #12; `NETWORK_PARTITION` (critical) + `PARTITION_RESOLVED` (info) alerts
- **Bulk Fleet Operations** (MESH-059): `BulkOperationManager` with preview (dry-run), execute, cancel, progress tracking; 5 API endpoints (`/bulk-ops/*`); safety gate: `dry_run=True` default, requires explicit `confirmed=True`; supports config_push, reboot, psk_rotation, firmware_update, factory_reset
- DB schema v14: 6 new tables (`webhooks`, `webhook_deliveries`, `notification_channels`, `notification_rules`, `partition_events`, `bulk_operations`) with ~34 CRUD methods
- 3 new AlertType values: `ENCRYPTION_WEAK` (warning), `NETWORK_PARTITION` (critical), `PARTITION_RESOLVED` (info) — 31 total
- Health endpoint: 17 component checks (was 12); watchdog: 12 checks (was 10)
- 144 API routes (was 119)

## [0.5.0] - 2026-03-03

### Added

- **Ollama — Intelligent Provisioning Advisor** (MESH-032): `POST /api/v1/advisor/recommend` — AI-powered deployment recommendations with deterministic fallback; terrain-aware channel config, power-source-based TX power, ~30% router ratio for large fleets
- **Ollama — Lost Node Reasoning** (MESH-033): `GET /api/v1/locate/{node_id}/ai-reasoning` — probabilistic location analysis using GPS history, battery level, movement vector, topology edges; compass-direction movement analysis; search recommendations
- **Environmental Telemetry Aggregation** (MESH-039): 5 API endpoints (`/environment/*`) — ingest temp, humidity, pressure, air quality from Meshtastic sensors; configurable thresholds → `ENV_THRESHOLD_EXCEEDED` fleet alerts; fleet-wide summary with per-node latest readings
- DB schema v13: `env_telemetry` table with 5 CRUD methods
- 52 new tests (1,310 total)

## [0.4.0] - 2026-03-03

### Added

- **Ollama Integration Foundation**: `OllamaClient` async wrapper (`inference/ollama_client.py`) — shared across anomaly detection, alert summarization, provisioning advisor, and lost node reasoning; configurable via `OLLAMA_HOST` / `OLLAMA_MODEL` env vars; graceful degradation when Ollama unavailable
- **Anomaly Detection** (MESH-017): `AnomalyDetector` uses Ollama to analyze telemetry deviations against baselines; fleet-wide anomaly scanning; 4 API endpoints (`/anomaly/*`)
- **Alert Summarization** (MESH-018): `AlertSummarizer` collapses active alerts into AI-generated summaries; per-node and fleet-wide summaries; 3 API endpoints (`/alerts/summary/*`)
- **Geofencing Alerts** (MESH-019): `GeofencingManager` with point-in-circle (Haversine) and point-in-polygon (ray casting) checks; circle and polygon fence types; entry/exit/both triggers; 6 API endpoints (`/geofences/*`)
- **Topology Visualization** (MESH-024): Interactive D3.js force-directed graph at `/topology`; color-coded online/offline/degraded nodes; edge thickness proportional to SNR; SPOF pulsing highlight; click-to-inspect sidebar
- **Mesh Coverage Mapping** (MESH-034): `CoverageMapper` aggregates RSSI observations into heatmap grid cells; dead zone detection; GeoJSON export; Leaflet heatmap overlay; 4 API endpoints (`/coverage/*`)
- **Fleet Analytics Dashboard** (MESH-035): `FleetAnalytics` with uptime trends, battery trends, alert frequency, message volume, fleet growth; SVG sparklines; 5 API endpoints (`/analytics/*`)
- DB schema v12: `geofences` + `coverage_samples` tables with 10 CRUD methods; 6 new AlertType values
- `[ollama]` extra dependency group in pyproject.toml
- 406 new tests (1,258 at v0.4.0 completion, before v0.5.0 additions)

## [0.3.0] - 2026-03-03

### Added

- **Production Readiness Hardening** (MESH-047, P0): SecurityHeaders, RequestLogging, RateLimiting (120 req/min), CORS middleware stack; global error handlers (404/400/422/500); lifespan startup/shutdown with graceful degradation; structured rotating log handler (10MB × 5); comprehensive `/health` endpoint with DB + subsystem components; fixed 12× HTTP 200 error antipattern across 8 route files
- **Mesh-Based Edge Node Recovery** (MESH-025): Send recovery commands (reboot, restart_service, system_status) to offline edge nodes via LoRa mesh; MQTT relay with wire protocol (`RECOVER|{cmd_id}|{type}|{args}|{nonce}|{ts}`); hardcoded ALLOWED_COMMANDS/SERVICES, nonce+timestamp replay prevention, 30s rate limit, `confirmed: true` API gate
- **Emergency Broadcast System** (MESH-026): Push critical alerts to all field operators via mesh when cloud is down; 4 emergency types (evacuation, network_down, severe_weather, security_alert); MQTT mesh echo delivery confirmation
- **Edge Node Heartbeat via Mesh** (MESH-031): Agent sends `HEARTBEAT|nodeId|uptime|services|battery` via mesh text when internet is down; dashboard shows "reachable via mesh" status; distinguishes internet-down from truly-dead
- **Store-and-Forward Config Queue** (MESH-028): Outbox pattern for offline radio config delivery; exponential backoff retries (1m → 32m cap, max 10 retries); BulkPushManager auto-enqueues failures; 5 API endpoints for queue visibility
- **Config Drift Auto-Remediation** (MESH-023): Auto-detect and one-click fix config drift via RemoteAdmin push; preview/remediate/remediate-all/status endpoints; integrates with config queue for retry
- **Automated Failover** (MESH-029): When relay SPOF goes offline, assess impact, apply TX power/hop limit compensations to neighbors via RemoteAdmin, auto-revert on recovery; 7 API endpoints with confirmed gate pattern; battery guard (skip < 30% nodes)
- **Mesh Watchdog** (MESH-030): Background asyncio watchdog with 9 staggered health checks (offline nodes, stale heartbeats, low battery, health scoring, config drift, topology SPOF, failover recovery, baseline deviation, post-push failures); auto-create/resolve alerts; audit trail in `watchdog_runs` table
- **OTA Config Rollback** (MESH-040): Snapshot device config before every push, monitor for post-push failures, auto-rollback if node goes offline; `ConfigRollbackManager` integrated into BulkPushManager + DriftRemediationManager; 4 API endpoints (`/config-rollback/*`)
- DB schema v4 → v10 (6 migrations: recovery_commands, config_queue, failover_events/compensations, watchdog_runs, config_snapshots)
- 15 new AlertType values across recovery, failover, watchdog, and rollback subsystems
- 556 new tests (852 total, up from 296)

### Changed

- Sprint plan: MESH-047 promoted from v1.0.0 to v0.3.0 as P0; MESH-030 watchdog expanded from 8 to 9 checks with OTA rollback integration
- `/health` endpoint now reports 10 subsystem components (DB, workbench, bulk_push, config_queue, drift_remediation, failover, mesh_watchdog, config_rollback, recovery, emergency)
- CLAUDE.md: added TODO comment policy (every TODO must have matching ADO work item)

## [0.2.0] - 2026-03-02

### Added

- **Mesh Topology Mapping** (MESH-016): Real-time mesh topology graph from NEIGHBORINFO neighbor data, directed edge storage (asymmetric LoRa links), schema v2 migration, Tarjan's articulation point algorithm for single-point-of-failure detection, connected component analysis, 5 new API endpoints
- **Radio Performance Baselines** (MESH-020): Rolling 7-day baselines per node (RSSI, SNR, battery drain, telemetry interval), 2σ deviation detection, schema v3 migration with telemetry_history and device_baselines tables
- **Firmware Compatibility Matrix** (MESH-021): Track firmware↔hardware compatibility, safe-to-flash checks, fleet upgrade scanning via Meshtastic GitHub releases API
- **Radio Health Scoring** (MESH-022): Composite 0-100 health score per node (uptime 30%, signal 25%, battery 20%, config 15%, firmware 10%), fleet health summary and histogram
- **Radio Workbench** (MESH-066): Single-radio config builder dashboard page — connect/read/edit/test/save-as-template workflow, interactive form editor by category, diff preview, bulk push via RemoteAdmin CLI with progress tracking, 9 API endpoints
- **Physical Deployment** (MESH-067): ARM64 Linux mesh appliance deployment — 4 systemd services (broker, dashboard, agent, sentry sidecar), 9-phase idempotent installer, udev rules for Meshtastic USB devices, Mosquitto production config, package release script, SSH deploy pipeline, health check, nightly SQLite backup, UFW firewall, operator guide
- 166 new tests (296 total)

### Changed

- Sprint plan reorganized by priority: P0 Production Hardening (MESH-047) promoted to v0.3.0, P2 Ollama/Geofencing items (MESH-017/018/019) deferred to v0.4.0
- CI pipeline updated with Release stage for ARM64 tarball packaging

## [0.1.0] - 2026-03-01

### Added

- Initial project scaffold with pyproject.toml, jenn-contract.json, CLAUDE.md
- Core models: MeshDevice, ChannelConfig, FleetHealth, GPSPosition
- SQLite WAL database with device registry, positions, alerts, config templates
- Golden config YAML templates for 4 device roles (relay, gateway, mobile, sensor)
- PKC admin key generation and Managed Mode setup utilities
- Bench provisioning CLI (`jenn-mesh provision`) for USB flash of golden configs
- Agent radio bridge for serial/TCP/BLE connection to local Meshtastic radio
- Dedicated Mosquitto broker configuration (port 1884, TLS + auth)
- MQTT subscriber for telemetry ingestion (NodeInfo, Position, Telemetry packets)
- Fleet health monitoring with offline/low-battery alert detection
- Lost node locator with GPS position aggregation and proximity search
- FastAPI dashboard at mesh.jenn2u.ai with Thinking Canvas design system
- Dashboard pages: Fleet Map, Device List, Config Manager, Provisioning, Locator, Alerts
- Pre-commit hooks (Black, Flake8, Bandit, contract version check)
- Comprehensive test suite (models, core, agent, provisioning, locator, dashboard)
