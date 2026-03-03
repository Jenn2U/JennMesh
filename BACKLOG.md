# JennMesh — Development Backlog

*Part of the JENN Intelligent Ecosystem — Centralized Meshtastic LoRa radio fleet management.*

**Last Updated**: 2026-03-02 (v0.2.0 released)
**Current Version**: 0.2.0

---

## Priority Definitions

- **P0 (Critical)**: Blockers for production deployment
- **P1 (High)**: Important for production quality
- **P2 (Medium)**: Enhances capability significantly
- **P3 (Low)**: Nice-to-have improvements

## Effort Estimates

- XS: 1-2 hours | S: 2-4 hours | M: 4-8 hours | L: 8-16 hours | XL: 16-24 hours | XXL: 24-40 hours

## Sprint Planning

| Version | Sprint | Theme | Status |
|---------|--------|-------|--------|
| **v0.1.0** | Sprint 1-2 | Foundation — scaffold, models, provisioning, agent, MQTT, basic dashboard | ✅ Released |
| **v0.2.0** | Sprint 3-4 | Intelligence — topology, baselines, health scoring, workbench, physical deploy | ✅ Released |
| **v0.3.0** | Sprint 5-6 | Hardening & Resilience — production hardening (P0), mesh recovery, emergency broadcast, heartbeat | **Next Up** |
| **v0.4.0** | Sprint 7-8 | Intelligence & Analytics — Ollama, geofencing, mesh watchdog, coverage mapping | Planned |
| **v0.5.0** | Sprint 9-10 | Automation — failover, TAK integration, OTA rollback, mesh relay sync | Planned |
| **v1.0.0** | Sprint 11-12 | Integration & GA — iJENN2u, team comms, webhooks, API versioning | Planned |
| **v1.1.0+** | Ongoing | Advanced — ML predictions, multi-mesh bridging, satellite integration | Backlog |

---

## ═══════════════════════════════════════════════════
## v0.1.0 — FOUNDATION (Sprint 1-2)
## ═══════════════════════════════════════════════════

### MESH-001: Project Scaffold & Core Models
**Priority**: P0 | **Effort**: M | **Status**: Done
Create repo structure, pyproject.toml, CLAUDE.md, jenn-contract.json, VERSION, CHANGELOG.
Core Pydantic models: MeshDevice, ChannelConfig, FleetHealth, GPSPosition.
Pre-commit hooks, .flake8, contract version checker.

### MESH-002: SQLite WAL Database Layer
**Priority**: P0 | **Effort**: M | **Status**: Done
Schema v1: devices, positions, alerts, config_templates, provisioning_log, channels.
WAL mode for concurrent read/write. MeshDatabase class with full CRUD operations.
Position pruning, alert dedup, upsert logic.

### MESH-003: Golden Config YAML Templates
**Priority**: P0 | **Effort**: S | **Status**: Done
4 role-based templates: relay-node, edge-gateway, mobile-client, sensor-node.
Common base: PKC admin key, encrypted channels, MQTT to dedicated broker with TLS.
Per-role overrides: device.role, GPS, display, power, bluetooth, wifi settings.

### MESH-004: PKC Security Setup
**Priority**: P0 | **Effort**: M | **Status**: Done
Fleet admin PKC keypair generation (stored in Azure Key Vault).
Admin public key injection into golden configs.
Managed Mode enable/disable functions.
Security audit: ensure no PSKs or private keys in logs or error messages.

### MESH-005: Bench Provisioning CLI
**Priority**: P0 | **Effort**: L | **Status**: Done
Interactive USB provisioning flow: detect device, read current config, select role,
apply golden template + admin keys + channels, verify, register in DB.
Non-interactive mode: `jenn-mesh provision --role relay --port /dev/ttyUSB0`.
Rich terminal output with progress indicators.

### MESH-006: Agent Radio Bridge
**Priority**: P0 | **Effort**: L | **Status**: Done
Wraps `meshtastic` Python library. Connects via serial, TCP, or BLE.
Subscribes to mesh packets (NodeInfo, Telemetry, Position, Text).
Forwards telemetry to dedicated MQTT broker.
Reconnection logic with exponential backoff.
Lightweight daemon: `pip install "jenn-mesh[agent]"`, systemd/launchd service.

### MESH-007: Dedicated MQTT Broker Configuration
**Priority**: P0 | **Effort**: M | **Status**: Done
Mosquitto config (port 1884): TLS certificates, username/password auth, topic ACLs.
Docker compose for local dev (broker + dashboard).
Production deployment: Docker on NAS or Azure Container App.
Topic namespace: `jenn/mesh/{region}/json/{channel}/{nodeId}`.

### MESH-008: MQTT Telemetry Subscriber
**Priority**: P0 | **Effort**: L | **Status**: Done
Subscribes to `jenn/mesh/#` on dedicated broker.
Packet handlers: NodeInfo → device registry, Position → positions table,
Telemetry → battery/signal/environment updates.
Offline detection: configurable threshold (default 600s).
Low battery alerts, signal degradation alerts.

### MESH-009: Device Registry & Fleet Health
**Priority**: P0 | **Effort**: M | **Status**: Done
DeviceRegistry class wrapping MeshDatabase with domain logic.
Fleet health aggregation: online/offline/degraded counts, alert summaries.
Health score computation (0-100%). Alert severity mapping.
Periodic health check loop (check offline, check battery, check drift).

### MESH-010: Lost Node Locator
**Priority**: P1 | **Effort**: M | **Status**: Done
GPS position aggregation from mesh.
Haversine distance calculations for proximity search.
Correlate edge node device IDs with radio node IDs.
API: `GET /api/v1/locate/{nodeId}` → last position + confidence + nearby active nodes.
Confidence levels: high (fresh GPS, < 1h), medium (stale, 1-24h), low (> 24h or no GPS).

### MESH-011: FastAPI Dashboard — Basic
**Priority**: P1 | **Effort**: XL | **Status**: Done
FastAPI at port 8002 with Jinja2 + vanilla JS.
Thinking Canvas design: teal #0D7377, amber #D97706, DM Sans/Inter/JetBrains Mono.
Pages: Fleet Map (Leaflet/OpenStreetMap), Device List (sortable table),
Config Manager (template viewer), Provisioning Log, Lost Node Locator, Alerts.
Health endpoint: `GET /health` for JennSentry monitoring.
Cache-Control: no-store middleware for all API endpoints.

### MESH-012: CLI Commands Suite
**Priority**: P1 | **Effort**: M | **Status**: Done
`jenn-mesh provision` — bench flash (interactive + non-interactive).
`jenn-mesh fleet list` — device table. `jenn-mesh fleet health` — summary.
`jenn-mesh config drift` — drift report. `jenn-mesh locate <nodeId>`.
`jenn-mesh serve` — start dashboard. `jenn-mesh agent` — start agent daemon.
`jenn-mesh channels show` — display current channel set.

### MESH-013: Azure Pipeline & Infrastructure
**Priority**: P1 | **Effort**: L | **Status**: Done
azure-pipelines.yml: Lint (Black/Flake8/Bandit) → Test → Build → Deploy.
Container App Bicep: `jennmesh-{env}`, resource group `jenn-ai-rg`.
Front Door route: `mesh.jenn2u.ai`. DNS CNAME in GoDaddy script.
GitHub mirror: `Jenn2U/JennMesh` for Advanced Security scanning.

### MESH-014: Ecosystem Integration
**Priority**: P1 | **Effort**: M | **Status**: Done
Update PORT_ALLOCATION.md (8002, 1884).
Update CROSS_PROJECT_CONTRACT.md Section 7 (version matrix).
Add JennMesh to `/jenn-pipeline-status` and `/watchdog` skill coverage.
Update auto-memory (jenn-ecosystem.md).
Add JennSentry infrastructure monitoring endpoint for mesh.jenn2u.ai.

### MESH-015: Test Suite — Foundation
**Priority**: P1 | **Effort**: L | **Status**: Done (130 tests)
Unit tests: models, db, registry, config_manager, channel_manager, locator.
Integration tests: mock radio bridge, mock MQTT subscriber.
Dashboard tests: FastAPI TestClient for all routes.
Target: 80%+ coverage. All tests mock hardware — no real radios needed.

---

## ═══════════════════════════════════════════════════
## v0.2.0 — INTELLIGENCE (Sprint 3-4) ✅ RELEASED
## ═══════════════════════════════════════════════════

### MESH-016: Mesh Topology Mapping
**Priority**: P1 | **Effort**: L | **Status**: Done
Build real-time mesh topology graph from NEIGHBORINFO neighbor data.
Directed edge storage (asymmetric LoRa links), schema v2 migration.
Identify single points of failure (relay X is the only path between clusters).
Identify coverage gaps and isolated segments.
Dashboard: interactive topology visualization (D3.js or similar).
API: `GET /api/v1/topology` → graph JSON.

### MESH-017: Ollama Integration — Anomaly Detection
**Priority**: P2 | **Effort**: L | **Status**: Deferred → v0.4.0
Optional `jenn-mesh[ollama]` extra (reuse JennEdge's Ollama on 11434).
Analyze telemetry time-series for unusual patterns:
  - Abnormal battery drain rates (hardware fault?)
  - Signal degradation patterns (environmental change? interference?)
  - Position drift (GPS interference? device moved?)
Prompt engineering for Meshtastic-specific anomaly descriptions.
Feed results into alert system with `anomaly_detected` alert type.

### MESH-018: Ollama Integration — Alert Summarization
**Priority**: P2 | **Effort**: M | **Status**: Deferred → v0.4.0
Collapse multiple related alerts into human-readable summaries.
Example: 47 alerts → "3 nodes in the north cluster lost connectivity —
likely relay node !2A3B went offline, which serves as the only hop between
the north and south clusters."
Runs on-demand and on alert threshold (>10 active alerts).
Dashboard: "AI Summary" panel on Alerts page.

### MESH-019: Geofencing Alerts
**Priority**: P2 | **Effort**: M | **Status**: Deferred → v0.4.0
Define operational zones as polygons or bounding boxes.
Alert when a mobile/tracker node exits its assigned zone.
Use cases: asset tracking, security perimeter, field team boundaries.
Configurable per device or per role.
Dashboard: zone editor overlay on Fleet Map.
API: `POST /api/v1/geofences`, `GET /api/v1/geofences`.

### MESH-020: Radio Performance Baselines
**Priority**: P2 | **Effort**: L | **Status**: Done
For each node, compute baselines over a learning window (7 days):
  - Typical RSSI range, SNR range, battery drain rate
  - Typical telemetry reporting interval
Alert on deviations from baseline (not just absolute thresholds).
A node at -90 RSSI dropping to -110 is more meaningful than a node always at -110.
Store baselines in `device_baselines` table (schema migration v2).

### MESH-021: Firmware Compatibility Matrix
**Priority**: P2 | **Effort**: S | **Status**: Done
Track which firmware versions support which hardware models.
When new firmware releases, auto-flag only compatible devices.
Prevent accidental flash of incompatible firmware during provisioning.
Data source: Meshtastic GitHub releases API or manual config file.
Dashboard: Firmware page with compatibility grid.

### MESH-022: Radio Health Scoring
**Priority**: P2 | **Effort**: M | **Status**: Done
Composite health score (0-100) per node based on:
  - Uptime percentage (last 30 days)
  - Signal quality (RSSI/SNR relative to baseline)
  - Battery health (drain rate, charge cycles if available)
  - Config compliance (drift = penalty)
  - Firmware currency (outdated = penalty)
Dashboard: health score badge per device, fleet-wide health histogram.

### MESH-023: Config Drift Auto-Remediation
**Priority**: P3 | **Effort**: M | **Status**: Done
When drift is detected, offer one-click remediation via remote admin:
  Push golden template to drifted device over the mesh using PKC admin.
  Log remediation in provisioning_log.
  Safety: show diff before push, require confirmation.
  Dashboard: "Fix Drift" button per device in Config Manager.

### MESH-024: Dashboard — Topology Visualization
**Priority**: P2 | **Effort**: L | **Status**: Backlog
Interactive graph view of mesh topology (D3.js force-directed or hierarchical).
Color nodes by status (online/offline/degraded).
Edge thickness by signal quality (RSSI).
Highlight single points of failure in red.
Click node → device detail panel.
Overlay on map or standalone view.

### MESH-065: TAK Server Integration — CoT Gateway
**Priority**: P2 | **Effort**: L | **Status**: Backlog
Bidirectional gateway between JennMesh and TAK (Team Awareness Kit) ecosystem.
**CoT Gateway Module** (`core/tak_gateway.py`):
  - Translate JennMesh device positions to CoT (Cursor on Target) XML events.
  - Publish CoT to TAK Server via TCP/UDP (port 8087/8089).
  - Ingest CoT events from TAK Server → JennMesh alerts and waypoints.
  - Leverage Meshtastic's built-in TAK plugin for LoRa-to-CoT bridging.
**Optional TAK Server Container** (`infra/tak-server.yaml`):
  - Docker Compose profile `tak` — opt-in, not default.
  - FreeTAKServer (Python-based, lighter than official Java TAK Server).
  - Pre-configured to peer with JennMesh's dedicated MQTT broker.
**Dashboard Integration**:
  - Fleet Map: toggle ATAK/WinTAK client markers alongside mesh nodes.
  - Alerts: surface TAK emergency markers as JennMesh alerts.
**Use cases**: SAR coordination, field team SA, interop with ATAK/WinTAK users.
**Dependency**: Meshtastic TAK plugin must be enabled on target radios.

### MESH-066: Radio Workbench — Single-Radio Config Builder
**Priority**: P1 | **Effort**: XL | **Status**: Done
Dedicated dashboard page for hands-on single-radio configuration and golden template creation.
**Connect** — select connection method (serial/USB, TCP, BLE) and connect to one radio.
**Read** — pull current device config (`--export-config` equivalent) and display as structured form.
**Edit** — interactive form editor grouped by category:
  - Device: role, node name, short name, hardware model
  - Channels: channel name, PSK (generate/paste), encryption, uplink/downlink
  - MQTT: broker address, port, TLS, credentials, topics, encryption
  - LoRa: region, modem preset, hop limit, TX power
  - GPS: enabled, update interval, position broadcast interval
  - Power: battery, sleep settings, LED control
  - Display: screen enabled, flip, OLED type, units
  - Security: admin key (PKC), managed mode, serial console access
**Test** — apply edited config to the connected radio, verify with read-back.
**Save as Template** — promote the tested config to a new golden template (named by role).
**Bulk Push** (links to Config Manager) — after saving, navigate to Config Manager
  to select target devices from fleet list and push via PKC remote admin.
**Dashboard nav**: top-level "Workbench" page alongside Fleet, Map, Config, etc.
**API routes**:
  - `POST /api/v1/workbench/connect` — initiate radio connection
  - `GET /api/v1/workbench/config` — read connected radio's config
  - `POST /api/v1/workbench/apply` — push edited config to connected radio
  - `POST /api/v1/workbench/save-template` — save current config as golden template
  - `GET /api/v1/workbench/status` — connection status + radio info
**Config Manager additions** (paired with this item):
  - "Push to Fleet" action: select template → pick devices → confirm → remote admin push
  - Progress tracker: show push status per device (queued/pushing/success/failed)
  - `POST /api/v1/config/push` — bulk push endpoint
**Safety**: diff preview before any apply/push, confirmation dialogs, audit trail in provisioning_log.

### MESH-067: Physical Deployment — Mesh Appliance on ARM64 Linux
**Priority**: P1 | **Effort**: XXL | **Status**: Done
Deploy JennMesh as an all-in-one "mesh appliance" on ARM64 Linux (Pi 5 / Orange Pi)
for physical USB/Bluetooth radio administration. Bare-metal + systemd (not Docker).
**Components**:
  - 4 systemd services: broker, dashboard, agent, sentry sidecar
  - 9-phase idempotent install script (follows JennEdge pattern)
  - Udev rules for stable /dev/meshtastic* USB symlinks (CP2102, CH9102, FTDI)
  - Mosquitto production config (auth, persistence, LAN-only)
  - Package release script for ARM64 tarballs
  - SSH-based deploy pipeline (Azure DevOps → physical server)
  - SQLite nightly backup via cron (14-day retention)
  - UFW firewall for LAN-only access (ports 8002, 1884)
  - Health check script (services, HTTP, MQTT, USB, DB)
  - Operator deployment guide (deploy/README.md)
**Directory layout**: `/opt/jenn-mesh/current` → versioned install, `/etc/jenn-mesh/` config,
  `/var/lib/jenn-mesh/` data, `/var/log/jenn-mesh/` logs.

---

## ═══════════════════════════════════════════════════
## v0.3.0 — HARDENING & RESILIENCE (Sprint 5-6)
## ═══════════════════════════════════════════════════

### MESH-047: Production Readiness Hardening ⬆️ PROMOTED from v1.0.0
**Priority**: P0 | **Effort**: XL | **Status**: ✅ Done
- Middleware stack: SecurityHeaders, RequestLogging, RateLimiting (120 req/min per-IP), CORS
- Global error handlers: HTTPException (404/400), RequestValidationError (422), unhandled (500)
- Lifespan management: `@asynccontextmanager` startup/shutdown with graceful degradation
- Structured logging: rotating file handler (10MB × 5 backups) + console
- Comprehensive health: DB + workbench + bulk_push + uptime + schema_version
- Fixed 12× HTTP 200 error antipattern across 8 route files → proper 404/400
- 34 new tests (middleware, error handlers, health, lifespan); 330 total passing
- 4 new production files: `logging_config.py`, `middleware.py`, `error_handlers.py`, `lifespan.py`

### MESH-025: Mesh-Based Edge Node Recovery
**Priority**: P1 | **Effort**: XL | **Status**: ✅ Done
Send recovery commands (reboot, restart_service, restart_ollama, system_status)
to offline edge nodes via LoRa mesh. Dashboard → MQTT → Gateway Agent → Mesh →
Target Agent → execute + ACK. Wire protocol: `RECOVER|{cmd_id}|{type}|{args}|{nonce}|{ts}`,
ACK: `RECOVER_ACK|{cmd_id}|{status}|{message}`. Channel 1 (ADMIN) with PSK encryption.
Safety: hardcoded ALLOWED_COMMANDS/SERVICES frozensets, nonce+timestamp replay prevention,
30s per-node rate limit, `confirmed: true` API gate, 5-min command expiry.
- 5 new source files: models/recovery.py, core/recovery_manager.py, agent/recovery_handler.py,
  agent/recovery_relay.py, dashboard/routes/recovery.py
- 8 modified files: db.py (schema v5→v6), fleet.py, mqtt_subscriber.py, cli.py,
  app.py, health.py, lifespan.py, conftest.py
- 5 test files, 141 new tests; 601 total passing
- DB schema v5→v6: `recovery_commands` table + 6 new DB methods
- 4 API endpoints: POST /recovery/send, GET /commands, GET /command/{id}, GET /status/{node_id}
**This is the killer feature** — no other system can recover offline edge nodes via radio mesh.

### MESH-026: Emergency Broadcast System
**Priority**: P1 | **Effort**: L | **Status**: ✅ Done
Push critical alerts to all field operators via mesh when cloud/internet is down.
Predefined emergency channels with broadcast capability.
Emergency message types: evacuation, network down, severe weather, security alert.
Dashboard: emergency broadcast panel with confirmation (irreversible action).
All radios display emergency messages regardless of current channel.
**Implemented**: Schema v5, EmergencyBroadcastManager, 4 API endpoints, MQTT mesh echo delivery confirmation, 65 tests.

### MESH-027: Mesh Relay for Edge Sync (Backup Path)
**Priority**: P2 | **Effort**: XXL | **Status**: Backlog
When internet is down, use mesh radios as a slow backup path for critical
CRDT sync operations between edge nodes and Production.
LoRa bandwidth is ~1-10 kbps — only sync high-priority deltas:
  - Conversation state (small CRDT items)
  - Device status changes
  - Critical config updates
Fragment large messages across multiple LoRa packets.
Requires: mesh-to-MQTT bridge at a gateway node with internet connectivity.
**Architectural significance**: Makes JENN resilient to complete internet outages.

### MESH-028: Store-and-Forward Config Queue
**Priority**: P2 | **Effort**: M | **Status**: Done ✅
When a radio is offline (relay lost power, device in transit), queue config changes.
Outbox pattern: `config_queue` table with target node, config payload, retry count.
Delivery confirmation: when node reconnects, push pending config via RemoteAdmin.
Exponential backoff for retries (1m → 2m → 4m → ... → 32m cap). Max retry limit (10)
with escalation to `CONFIG_PUSH_FAILED` fleet alert. Dashboard: 5 API endpoints for
queue visibility, manual retry/cancel, and device queue status. Background `asyncio`
retry loop processes pending entries every 30s. BulkPushManager auto-enqueues failures.
Schema v7 adds `config_queue` table + indexes. 55 new tests (656 total).

### MESH-029: Automated Failover
**Priority**: P2 | **Effort**: XL | **Status**: ✅ Done
When a relay SPOF goes offline, automated failover assesses impact, identifies
compensation nodes, applies config changes (TX power, hop limit, role) via
RemoteAdmin, and auto-reverts when the failed node recovers.
- `FailoverManager` in `core/failover_manager.py` — assess, execute, revert, cancel, check_recoveries
- `models/failover.py` — FailoverEvent, Compensation, ImpactAssessment models
- `routes/failover.py` — 7 API endpoints with confirmed gate pattern
- Topology extensions: `find_dependent_nodes()`, `find_alternative_paths()`, `get_compensation_candidates()`
- Schema v8: `failover_events` + `failover_compensations` tables, 8 new DB CRUD methods
- 3 new AlertTypes: FAILOVER_ACTIVATED, FAILOVER_REVERTED, FAILOVER_REVERT_FAILED
- `/health` includes failover component (#9)
- Battery guard: skip compensation candidates with < 30% battery
**Requires**: MESH-016 (topology mapping), MESH-025 (remote admin), MESH-028 (config queue).
65 new tests (753 total).

### MESH-030: Mesh Watchdog → Moved to v0.4.0
*See v0.4.0 section.*

### MESH-031: Edge Node Heartbeat via Mesh
**Priority**: P1 | **Effort**: M | **Status**: ✅ Done
If an edge node's internet is down but its radio is up, the JennMesh agent sends
a heartbeat via mesh text message: `HEARTBEAT|nodeId|uptime|services|battery`.
JennMesh dashboard shows "reachable via mesh" status for edge nodes.
JennSentry can consume this data to differentiate "internet down" from "truly dead".
**Critical for fleet visibility**: Internet-down ≠ dead. This distinction matters.

### MESH-032: Ollama — Intelligent Provisioning Advisor
**Priority**: P3 | **Effort**: L | **Status**: Backlog
"I'm deploying 5 nodes along a ridgeline. Recommend roles, power settings,
hop limits, and channel config."
Ollama analyzes: terrain (if elevation data available), existing topology,
desired coverage area, power constraints (solar? battery?).
Outputs: recommended golden config per node, deployment order, expected coverage.
Dashboard: provisioning wizard with AI recommendations.

### MESH-033: Ollama — Lost Node Reasoning
**Priority**: P3 | **Effort**: M | **Status**: Backlog
"Based on terrain, last GPS heading, battery level at last contact, and
environmental conditions, where is this node likely to be?"
Probabilistic location refinement beyond raw GPS data.
Considers: battery drain → estimated power-off time → maximum drift distance.
Movement vector from last N positions → projected path.
Dashboard: probability heatmap overlay on Lost Node Locator map.

---

## ═══════════════════════════════════════════════════
## v0.4.0 — INTELLIGENCE & ANALYTICS (Sprint 7-8)
## ═══════════════════════════════════════════════════

*Includes MESH-017/018/019 deferred from v0.2.0, plus analytics items.*

### MESH-017: Ollama Integration — Anomaly Detection (deferred from v0.2.0)
**Priority**: P2 | **Effort**: L | **Status**: Backlog
→ See v0.2.0 section for full description.

### MESH-018: Ollama Integration — Alert Summarization (deferred from v0.2.0)
**Priority**: P2 | **Effort**: M | **Status**: Backlog
→ See v0.2.0 section for full description.

### MESH-019: Geofencing Alerts (deferred from v0.2.0)
**Priority**: P2 | **Effort**: M | **Status**: Backlog
→ See v0.2.0 section for full description.

### MESH-030: Mesh Watchdog (moved from v0.3.0)
**Priority**: P2 | **Effort**: L | **Status**: Backlog
→ See v0.3.0 section for full description.

### MESH-024: Dashboard — Topology Visualization
**Priority**: P2 | **Effort**: L | **Status**: Backlog
→ See v0.2.0 section for full description.

### MESH-034: Mesh Coverage Mapping
**Priority**: P2 | **Effort**: XL | **Status**: Backlog
Build signal coverage heatmaps from collected RSSI/SNR data across the fleet.
As nodes communicate, each packet carries signal quality metadata.
Aggregate over time → coverage map showing strong/weak/dead zones.
Use cases: identify where new relays are needed, verify deployment coverage.
Dashboard: coverage heatmap overlay on Fleet Map.
Export: GeoJSON for external GIS tools.

### MESH-035: Fleet Analytics Dashboard
**Priority**: P2 | **Effort**: XL | **Status**: Backlog
Historical trends and fleet-wide statistics:
  - Uptime trends (per node, per cluster, fleet-wide)
  - Message volume and channel utilization over time
  - Battery health trends (declining capacity detection)
  - Alert frequency and resolution time
  - Provisioning activity and fleet growth
Inline SVG sparklines (same pattern as JennSentry infrastructure latency).
Date range selector. CSV export.

### MESH-036: Compliance Reporting
**Priority**: P3 | **Effort**: L | **Status**: Backlog
FCC/regulatory compliance tracking:
  - Transmit power within legal limits for region/frequency
  - Duty cycle compliance
  - Frequency band verification
  - Channel spacing
Generate compliance reports per device and fleet-wide.
Flag non-compliant configurations during provisioning and drift detection.
Dashboard: compliance status badge per device.

### MESH-037: Mesh Simulation / Planning Tool
**Priority**: P3 | **Effort**: XXL | **Status**: Backlog
Before physical deployment, simulate mesh topology with planned node locations.
Input: planned coordinates, hardware type, transmit power, terrain profile.
Output: estimated coverage, predicted RSSI between nodes, hop counts,
single points of failure, recommended adjustments.
Could use LoRa propagation models (line-of-sight + Fresnel zone).
Dashboard: drag-and-drop node placement on map with live simulation results.

### MESH-038: Automated Relay Placement Suggestions
**Priority**: P3 | **Effort**: L | **Status**: Backlog
Based on topology gaps, coverage dead zones, and terrain data:
  Suggest optimal locations for new relay nodes.
  Consider: existing coverage, terrain obstacles, power availability, access.
  Rank suggestions by impact (how many nodes benefit from this relay?).
**Requires**: MESH-016 (topology), MESH-034 (coverage mapping).

### MESH-039: Environmental Telemetry Aggregation
**Priority**: P2 | **Effort**: M | **Status**: Backlog
Meshtastic supports environment sensors: temperature, humidity, pressure, air quality.
Aggregate environmental data across the fleet for area-wide monitoring.
Use cases: warehouse climate monitoring, outdoor work site conditions,
agricultural monitoring, wildfire smoke detection.
Dashboard: environmental data overlay on Fleet Map.
API: `GET /api/v1/telemetry/environment` with spatial/temporal filters.

### MESH-040: Over-the-Air Config Rollback
**Priority**: P2 | **Effort**: M | **Status**: Backlog
Before every config push, snapshot the device's current config.
If a config push breaks a node (goes offline within N minutes of push):
  Auto-rollback to last known good config via remote admin.
  Log rollback event. Alert operator.
  Store config history per device in `config_snapshots` table.
**Safety net**: prevents bricking a device with a bad config push.

### MESH-041: Radio Audit Trail Enhancement
**Priority**: P3 | **Effort**: S | **Status**: Backlog
Extend provisioning_log into a full audit trail:
  - Every config change (who, when, what changed)
  - Every firmware update attempt (success/fail/rollback)
  - Every remote admin command sent
  - Every security event (admin key change, managed mode toggle)
Tamper-evident: SHA-256 chain linking each log entry to the previous one.
Exportable for compliance audits.

---

## ═══════════════════════════════════════════════════
## v1.0.0 — INTEGRATION & GA (Sprint 11-12)
## ═══════════════════════════════════════════════════

### MESH-042: iJENN2u Mobile Integration
**Priority**: P2 | **Effort**: XL | **Status**: Backlog
iJENN2u mobile app shows nearby mesh nodes on a map.
Send/receive mesh text messages from the phone via BLE to a nearby radio.
View fleet health summary in the mobile app.
Lost node locator accessible from mobile (field search assistance).
**Requires**: iJENN2u API client additions, BLE bridge in mobile app.

### MESH-043: Team Communication via Mesh
**Priority**: P2 | **Effort**: L | **Status**: Backlog
Text messaging through the mesh for field teams.
Message history stored in JennMesh database (not just on device).
Channels: per-team, emergency, broadcast.
Dashboard: chat-style message viewer with send capability.
MQTT bridge: messages also available on the dedicated broker for integration.

### MESH-044: External System Webhooks
**Priority**: P2 | **Effort**: M | **Status**: Backlog
Configurable webhook notifications for fleet events:
  - Node online/offline transitions
  - Alert creation/resolution
  - Geofence entry/exit
  - Emergency broadcasts
Targets: Teams, Slack, generic HTTP, IFTTT, Node-RED.
Webhook config UI in dashboard.

### MESH-045: Home Assistant / Node-RED Integration
**Priority**: P3 | **Effort**: M | **Status**: Backlog
MQTT-based integration with Home Assistant and Node-RED.
Publish device state changes, sensor data, alerts to HA-friendly MQTT topics.
HA entities: binary_sensor (online/offline), sensor (battery, signal, temp),
device_tracker (GPS position).
Node-RED: flow templates for common automations.

### MESH-046: Ollama — Natural Language Fleet Queries
**Priority**: P3 | **Effort**: L | **Status**: Backlog
"Which nodes near the warehouse are running firmware < 2.5?"
"Show me all offline nodes that were online yesterday."
"What's the battery trend for the mountaintop relay this week?"
Ollama parses natural language → SQL/API query → formatted response.
Dashboard: chat-style query interface.
CLI: `jenn-mesh ask "..."`.

### MESH-047: Production Readiness Hardening → ✅ Done in v0.3.0
*Promoted from v1.0.0 and completed — see v0.3.0 section.*

### MESH-048: Multi-Tenant Support
**Priority**: P3 | **Effort**: XXL | **Status**: Backlog
Support multiple independent mesh networks from a single JennMesh instance.
Use case: managing radios across different sites/customers/environments.
MQTT root topic per tenant: `jenn/mesh/{tenant_id}/...`.
Database: tenant_id column on all tables.
Dashboard: tenant selector, per-tenant views.
Auth: per-tenant API keys or JWT claims.

---

## ═══════════════════════════════════════════════════
## v1.1.0+ — ADVANCED (Ongoing)
## ═══════════════════════════════════════════════════

### MESH-049: Multi-Mesh Bridging
**Priority**: P3 | **Effort**: XXL | **Status**: Backlog
Connect separate mesh networks operating on different frequencies or regions
via the MQTT bridge. Mesh A ↔ MQTT Broker ↔ Mesh B.
Use case: geographically separated sites sharing a single management plane.
Requires: per-mesh MQTT topics, message dedup across meshes, routing policy.
Dashboard: multi-mesh topology view.

### MESH-050: Satellite Gateway Integration
**Priority**: P3 | **Effort**: XXL | **Status**: Backlog
Meshtastic supports satellite modems (Rockblock/Iridium, SWARM).
JennMesh could manage satellite-connected relay nodes:
  - Configure satellite modem parameters
  - Monitor satellite link quality and message delivery
  - Optimize which data goes over satellite (expensive per-byte) vs. mesh (free)
Use case: truly remote deployments with no terrestrial connectivity.

### MESH-051: ML-Based Predictive Maintenance
**Priority**: P3 | **Effort**: XXL | **Status**: Backlog
Train models on historical telemetry to predict:
  - Battery failure (capacity degradation curve → estimated failure date)
  - Hardware failure (signal quality anomalies preceding radio failure)
  - Environmental damage (moisture ingress → corrosion → performance drop)
Feed predictions into alert system: "Node !2A3B battery predicted to fail in 5 days."
**Requires**: sufficient historical data (6+ months) for training.

### MESH-052: Mesh-as-Sentry-Backup
**Priority**: P2 | **Effort**: L | **Status**: Backlog
When JennSentry agent on an edge node can't reach Production (internet down),
relay critical sentry alerts through the mesh to a gateway node that has connectivity.
Alert payload: compact JSON → mesh text message → MQTT bridge → JennSentry dashboard.
Ensures monitoring continuity even during internet outages.
**This closes a major resilience gap in the JENN architecture.**

### MESH-053: Mesh-Based Asset Tracking
**Priority**: P2 | **Effort**: L | **Status**: Backlog
Track vehicles, equipment, and personnel via mesh-connected GPS trackers.
Devices with TRACKER role report position at configurable intervals.
Dashboard: asset tracking view with trail history, speed, heading.
Geofence integration: alert when asset leaves zone.
Fleet management: assign assets to zones, teams, or projects.

### MESH-054: Channel Utilization Analytics
**Priority**: P3 | **Effort**: M | **Status**: Backlog
Monitor channel airtime utilization across the mesh.
Detect: congestion (too many devices on one channel), collision risk,
underutilized channels that could absorb traffic.
Recommend: channel rebalancing, frequency/spreading factor adjustments.
Dashboard: channel utilization heatmap (time × channel × load).

### MESH-055: Mesh Network Partitioning Detection
**Priority**: P2 | **Effort**: L | **Status**: Backlog
Detect when the mesh splits into disconnected segments (network partition).
Distinguish from simple node failure — "the north cluster is isolated" vs.
"one node went offline."
Alert with topology diff: before/after partition visualization.
Recommend: which relay to add/move to reconnect segments.
**Requires**: MESH-016 (topology mapping).

### MESH-056: Power-Optimized Scheduling
**Priority**: P3 | **Effort**: L | **Status**: Backlog
For solar-powered relay stations:
  - Monitor solar charge, battery voltage, power consumption
  - Dynamically adjust transmit power and duty cycle based on available energy
  - Predict overnight battery drain → reduce Tx power before sunset if needed
  - Morning recovery: increase Tx power as solar charges battery
Remote admin commands to adjust power settings via PKC.

### MESH-057: JennEdge Cross-Reference
**Priority**: P2 | **Effort**: M | **Status**: Backlog
Maintain a mapping between JennEdge device IDs and their associated radio node IDs.
When JennEdge reports a device, JennMesh knows which radio is physically co-located.
Enables: "Edge node X is offline — but its radio is still transmitting from GPS coords Y"
Bidirectional: JennEdge health page shows "Radio: online, signal good, battery 78%"
API: `GET /api/v1/fleet/{nodeId}/edge-association`.

### MESH-058: Mesh Message Encryption Audit
**Priority**: P2 | **Effort**: S | **Status**: Backlog
Verify that all channels have encryption enabled (PSK ≠ default).
Detect devices using the default "LongFast" unencrypted channel.
Flag devices with `mqtt.encryption_enabled: false` (unencrypted MQTT relay).
Compliance alert: "Node !2A3B is transmitting unencrypted on channel 0."
Dashboard: encryption status badge per device and fleet-wide encryption score.

### MESH-059: Bulk Fleet Operations
**Priority**: P2 | **Effort**: L | **Status**: Backlog
Batch operations across multiple devices:
  - Push config to all devices matching a role
  - Rotate PSK for a channel across the entire fleet
  - Schedule firmware update window for N devices
  - Bulk associate edge nodes with radio nodes
Dashboard: multi-select + batch action buttons.
Safety: dry-run preview → confirmation → execute with progress tracking.

### MESH-060: Notification Channels (Teams, Slack, Email)
**Priority**: P2 | **Effort**: L | **Status**: Backlog
Following Jenn's notification channel pattern (Telegram retired in v6.3.0):
  - Slack webhooks (Block Kit)
  - Teams Adaptive Cards via webhook
  - Email (SMTP)
Configurable per alert type and severity.
Dashboard: notification settings page.

### MESH-061: Dashboard — Dark Mode & Responsive
**Priority**: P3 | **Effort**: M | **Status**: Backlog
Dark mode toggle (prefers-color-scheme support).
Responsive layout for tablet/mobile use in the field.
PWA manifest for installable web app.
Offline-capable dashboard (cached last known state).

### MESH-062: Mesh Latency Profiling
**Priority**: P3 | **Effort**: M | **Status**: Backlog
Measure round-trip latency between admin node and each managed device.
Periodic ping via mesh → measure response time.
Track latency trends over time per device.
Dashboard: latency histogram, per-node latency sparklines.
Use for: hop count optimization, relay performance validation.

### MESH-063: Disaster Recovery Playbook
**Priority**: P2 | **Effort**: M | **Status**: Backlog
Automated disaster recovery scenarios:
  - "Internet down": activate mesh-as-backup for critical services
  - "Relay chain broken": trigger automated failover sequence
  - "Mass firmware failure": coordinate rollback across affected devices
  - "Security breach": rotate all PSKs fleet-wide, revoke compromised admin key
Each scenario: pre-defined steps, one-click activation, progress tracking.
Dashboard: DR playbook page with scenario cards.

### MESH-064: API Versioning & OpenAPI Spec
**Priority**: P2 | **Effort**: S | **Status**: Backlog
OpenAPI 3.0 spec auto-generated from FastAPI.
API versioning: `/api/v1/...` namespace.
Swagger UI at `/docs` and ReDoc at `/redoc`.
Client SDK generation for Python and TypeScript.

---

## ═══════════════════════════════════════════════════
## IDEAS PARKING LOT (Unscoped — Evaluate Later)
## ═══════════════════════════════════════════════════

### MESH-IDEA-001: LoRa Spectrum Analyzer
Tap into radio's raw RF data for real-time spectrum analysis.
Detect interference from non-Meshtastic sources.
Visualize: waterfall plot, spectrum density.
**Feasibility TBD**: depends on Meshtastic firmware exposing raw RF data.

### MESH-IDEA-002: Mesh-to-Voice Bridge
Route voice-like communications (compressed audio) through the mesh.
LoRa bandwidth is very limited — would require extreme compression (Codec2?).
Use case: emergency voice comm when all other channels are down.
**Feasibility TBD**: LoRa bandwidth may be insufficient for real-time voice.

### MESH-IDEA-003: Federated Mesh Management
Multiple JennMesh instances managing different mesh networks,
sharing fleet health data via CRDT sync (reusing JENN's sync protocol).
Hierarchical: site-level JennMesh → regional aggregator → global view.
**Feasibility TBD**: significant complexity, evaluate after v1.0.0.

### MESH-IDEA-004: Digital Twin
Full digital twin of the mesh network — real-time virtual replica.
Simulate "what if" scenarios: what happens if we remove node X?
What if we add a relay here? What if traffic doubles?
**Feasibility TBD**: requires MESH-037 (simulation) as foundation.

### MESH-IDEA-005: AI Mesh Optimizer
Continuous optimization loop:
  - Monitor fleet performance
  - Suggest topology/config changes
  - Simulate impact before applying
  - Apply approved changes
  - Measure results
  - Learn and improve
Autonomous mesh network that self-optimizes over time.
**Feasibility TBD**: requires v0.4.0 analytics + v0.3.0 automation foundations.

### MESH-IDEA-006: Integration with Meshtastic Web Client
Embed or integrate with the official Meshtastic web client for direct device
management alongside JennMesh fleet management.
**Feasibility TBD**: licensing and embedding compatibility.

### MESH-IDEA-007: Edge Compute Offload via Mesh
Use mesh radios to distribute lightweight compute tasks across edge nodes.
Example: distribute environmental sensor aggregation across multiple nodes
instead of sending all raw data to one gateway.
**Feasibility TBD**: LoRa bandwidth constraints make this very limited.

---

## Completed

### v0.1.0 — Foundation (Sprint 1-2) — ALL DONE
| ID | Title | Effort |
|----|-------|--------|
| MESH-001 | Project Scaffold & Core Models | M |
| MESH-002 | SQLite WAL Database Layer | M |
| MESH-003 | Golden Config YAML Templates | S |
| MESH-004 | PKC Security Setup | M |
| MESH-005 | Bench Provisioning CLI | L |
| MESH-006 | Agent Radio Bridge | L |
| MESH-007 | Dedicated MQTT Broker Configuration | M |
| MESH-008 | MQTT Telemetry Subscriber | L |
| MESH-009 | Device Registry & Fleet Health | M |
| MESH-010 | Lost Node Locator | M |
| MESH-011 | FastAPI Dashboard — Basic | XL |
| MESH-012 | CLI Commands Suite | M |
| MESH-013 | Azure Pipeline & Infrastructure | L |
| MESH-014 | Ecosystem Integration | M |
| MESH-015 | Test Suite — Foundation (130 tests) | L |

**Delivered**: 76 source files, 130 tests, 7,182 lines. Scaffold through dashboard, full infra (Container App Bicep, Front Door, DNS), ecosystem integration across all 7 JENN projects.

### v0.2.0 — Intelligence (Sprint 3-4) — ALL DONE (6/9 shipped, 3 deferred to v0.4.0)
| ID | Title | Effort |
|----|-------|--------|
| MESH-016 | Mesh Topology Mapping | L |
| MESH-020 | Radio Performance Baselines | L |
| MESH-021 | Firmware Compatibility Matrix | S |
| MESH-022 | Radio Health Scoring | M |
| MESH-066 | Radio Workbench — Single-Radio Config Builder | XL |
| MESH-067 | Physical Deployment — Mesh Appliance on ARM64 Linux | XXL |

**MESH-016 delivered**: Directed edge storage (topology_edges table), schema v2 migration, TopologyManager with Tarjan's articulation point algorithm, connected component analysis, MQTT NEIGHBORINFO handler, 5 API endpoints, 45 new tests (175 total).

**Sprint 3 delivered (MESH-020 + MESH-021 + MESH-022)**: Schema v3 migration (telemetry_history, device_baselines, firmware_compat tables). BaselineManager with rolling 7-day stats, deviation detection (2σ threshold), pure Python statistics. FirmwareTracker with compatibility matrix, safe-to-flash checks, fleet upgrade scanning. HealthScorer with 5 weighted factors (uptime 30%, signal 25%, battery 20%, config 15%, firmware 10%), composite 0-100 scores, fleet health summary. 12 new API endpoints. 73 new tests (248 total).

**MESH-066 delivered (Radio Workbench)**: WorkbenchManager singleton session (connect/disconnect/read/diff/apply/save-as-template) using meshtastic Python API for local radios. BulkPushManager with background-thread sequential push via RemoteAdmin CLI, cancellation, auto-cleanup. 9 new API endpoints (7 workbench + 2 bulk push), all async-bridged with `asyncio.to_thread()`. Thread-safe with `threading.Lock`. 48 new tests (296 total). Fixed operator-precedence bug in configs_dir handling that leaked test files.

**MESH-067 delivered (Physical Deployment)**: Full bare-metal deployment infrastructure for ARM64 Linux mesh appliances. 4 systemd services (broker, dashboard, agent, sentry sidecar). 9-phase idempotent install script following JennEdge's proven pattern. Udev rules for Meshtastic USB devices (CP2102, CH9102, FTDI) with auto-start. Mosquitto production config with password auth. Package release script, SSH deploy pipeline, health check, SQLite nightly backup (14-day retention), UFW firewall. 14 new deploy files. Operator guide in deploy/README.md.

**v0.2.0 Release Summary**: 6 items shipped. MESH-017/018/019 (Ollama, Geofencing) deferred to v0.4.0 — priority reprioritization pulled P0 production hardening (MESH-047) into v0.3.0 instead. 296 tests, 90+ source files.

### v0.3.0 — Hardening & Resilience (Sprint 5-6) — IN PROGRESS
| ID | Title | Effort | Status |
|----|-------|--------|--------|
| MESH-047 | Production Readiness Hardening | XL | ✅ Done |
| MESH-031 | Edge Node Heartbeat via Mesh | M | ✅ Done |
| MESH-026 | Emergency Broadcast System | L | ✅ Done |
| MESH-025 | Mesh-Based Edge Node Recovery | XL | ✅ Done |
| MESH-028 | Store-and-Forward Config Queue | M | ✅ Done |
| MESH-023 | Config Drift Auto-Remediation | M | ✅ Done |

**MESH-031 delivered**: Mesh heartbeat protocol (`HEARTBEAT|nodeId|uptime|services|battery|timestamp`, ~60-80 bytes, 120s interval). Schema v3→v4 migration (mesh_heartbeats table, 2 new devices columns). HeartbeatSender (agent side — build+send+interval gating via RadioBridge). HeartbeatReceiver (dashboard side — parse+store+stale detection). Registry intelligence: `INTERNET_DOWN` (warning) vs `NODE_OFFLINE` (critical) based on mesh reachability. 3 new API endpoints, fleet endpoint enrichment, health component. 65 new tests (395 total). 5 new source files, 10 modified.

**MESH-026 delivered**: Emergency broadcast system — push critical alerts to all field radios over LoRa mesh. Dashboard → MQTT → Agent → Mesh text (`[EMERGENCY:{TYPE}] {message}`). 6 emergency types, MQTT echo delivery confirmation, confirmed gate safety. Schema v5, EmergencyBroadcastManager, 4 API endpoints. 65 new tests (460 total).

**MESH-025 delivered**: Mesh-based edge node recovery — send reboot/restart commands to offline nodes via LoRa. Dashboard → MQTT → Gateway Agent → Mesh → Target Agent → execute + ACK. Wire protocol with nonce+timestamp replay prevention, 30s rate limit, hardcoded ALLOWED_COMMANDS frozenset. Schema v5→v6 (recovery_commands table). RecoveryManager, RecoveryHandler, RecoveryRelay. 4 API endpoints. 141 new tests (601 total).

**MESH-028 delivered**: Store-and-forward config queue — persistent outbox for offline radios. BulkPushManager auto-enqueues failures → ConfigQueueManager with exponential backoff (1m→32m cap) → RemoteAdmin retry via asyncio background loop. Max-retries escalation to CONFIG_PUSH_FAILED fleet alert. Schema v6→v7 (config_queue table). 5 API endpoints, manual retry/cancel with confirmed gate. 55 new tests (656 total).

**MESH-023 delivered**: Config drift auto-remediation — one-click fix for drifted devices. DriftRemediationManager coordinates ConfigManager + RemoteAdmin + ConfigQueueManager. Preview endpoint shows golden template YAML + hash comparison. Remediate pushes golden template via PKC admin; failures auto-enqueue in config queue for store-and-forward retry. Resolves both CONFIG_DRIFT and CONFIG_PUSH_FAILED alerts on success. Remediate-all batch operation. Status endpoint aggregates drift state, queue entries, alerts, recent log. 4 API endpoints with confirmed gate safety. 32 new tests (688 total).
