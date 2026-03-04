# Changelog

All notable changes to JennMesh will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
