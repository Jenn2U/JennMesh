# JennMesh — CLAUDE.md

## Project Overview

JennMesh is the centralized Meshtastic LoRa radio fleet management service for the JENN Intelligent Ecosystem. It handles initial radio provisioning, firmware tracking, channel/security configuration, MQTT relay setup, fleet health monitoring, and lost node location.

**Version**: 0.2.0
**Language**: Python 3.11+
**Type**: Standalone mesh management service with web dashboard + agent daemon + CLI tools
**Tests**: 601 (pytest) — target 80%+

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
| `src/jenn_mesh/core/bulk_push.py` | Bulk push golden templates to fleet via RemoteAdmin |
| `src/jenn_mesh/models/workbench.py` | Pydantic models for workbench + bulk push |
| `src/jenn_mesh/dashboard/routes/workbench.py` | 9 API endpoints (workbench + bulk push) |
| `src/jenn_mesh/dashboard/routes/heartbeat.py` | 3 API endpoints (per-device, recent, fleet mesh-status) |
| `src/jenn_mesh/dashboard/routes/emergency.py` | 4 API endpoints (send broadcast, list, get, fleet status) |
| `src/jenn_mesh/dashboard/routes/recovery.py` | 4 API endpoints (send command, list history, get by ID, node status) |
| `src/jenn_mesh/dashboard/app.py` | FastAPI dashboard application factory |
| `src/jenn_mesh/dashboard/middleware.py` | Security headers, request logging, rate limiting, CORS |
| `src/jenn_mesh/dashboard/error_handlers.py` | Global exception handlers (HTTP, validation, unhandled) |
| `src/jenn_mesh/dashboard/lifespan.py` | Application startup/shutdown lifecycle |
| `src/jenn_mesh/dashboard/logging_config.py` | Rotating file + console logging configuration |
| `src/jenn_mesh/cli.py` | CLI entry point with subcommands |
| `src/jenn_mesh/db.py` | SQLite WAL schema v6 (devices, positions, alerts, configs, heartbeats, emergency_broadcasts, recovery_commands) |
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
- `@asynccontextmanager` in `lifespan.py` — startup: logging, DB, WorkbenchManager, BulkPushManager, EmergencyBroadcastManager, RecoveryManager, startup_time
- Graceful degradation: if DB init fails, dashboard runs degraded (health reports "degraded")
- Test DB injection: `create_app(db=test_db)` sets state directly (httpx ASGITransport doesn't fire lifespan)

### Health Endpoint (`/health`)
- Components: database (schema_version), workbench, bulk_push, mesh_heartbeats, emergency_broadcasts, recovery_commands, uptime_seconds
- Overall status: "healthy" or "degraded" (if any component fails)

### Logging
- Rotating file handler: `/var/log/jenn-mesh/dashboard.log` (10MB × 5 backups, fallback to `./logs/`)
- Console handler to stderr; plain text format (not JSON)
- `configure_logging()` called during lifespan startup

## Mesh Heartbeat Subsystem (MESH-031)

Edge nodes send periodic heartbeat text messages over LoRa radio so JennMesh can differentiate "internet down but alive" from "truly dead" nodes.

### Wire Protocol
`HEARTBEAT|{nodeId}|{uptime_s}|{services}|{battery}|{timestamp}`
- ~60-80 bytes (well under LoRa 256-byte limit)
- Interval: 120 seconds (configurable via `--heartbeat-interval`)
- Services: comma-separated `name:status` pairs (e.g., `edge:ok,mqtt:down`)

### Database (Schema v4 → v5 adds emergency_broadcasts, v5 → v6 adds recovery_commands)
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
