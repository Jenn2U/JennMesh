# JennMesh — CLAUDE.md

## Project Overview

JennMesh is the centralized Meshtastic LoRa radio fleet management service for the JENN Intelligent Ecosystem. It handles initial radio provisioning, firmware tracking, channel/security configuration, MQTT relay setup, fleet health monitoring, and lost node location.

**Version**: 0.1.0
**Language**: Python 3.11+
**Type**: Standalone mesh management service with web dashboard + agent daemon + CLI tools
**Tests**: 296 (pytest) — target 80%+

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
| `src/jenn_mesh/models/fleet.py` | FleetHealth, NodeStatus, Alert, AlertType |
| `src/jenn_mesh/models/location.py` | GPSPosition, LostNodeQuery, ProximityResult |
| `src/jenn_mesh/core/registry.py` | SQLite WAL device registry |
| `src/jenn_mesh/core/config_manager.py` | Golden config CRUD, drift detection |
| `src/jenn_mesh/core/channel_manager.py` | PSK generation, channel distribution |
| `src/jenn_mesh/core/mqtt_subscriber.py` | Subscribes to mesh broker, ingests telemetry |
| `src/jenn_mesh/agent/radio_bridge.py` | Serial/TCP connection to local Meshtastic radio |
| `src/jenn_mesh/agent/remote_admin.py` | PKC remote admin commands via mesh |
| `src/jenn_mesh/provisioning/bench_flash.py` | USB detect + golden config flash |
| `src/jenn_mesh/provisioning/security.py` | PKC admin key gen, Managed Mode setup |
| `src/jenn_mesh/provisioning/firmware.py` | Firmware version tracking, update flagging |
| `src/jenn_mesh/locator/tracker.py` | GPS position aggregation from mesh |
| `src/jenn_mesh/locator/finder.py` | Lost node locator (last known + proximity) |
| `src/jenn_mesh/core/workbench_manager.py` | Single-radio workbench session (connect/read/edit/apply/save) |
| `src/jenn_mesh/core/bulk_push.py` | Bulk push golden templates to fleet via RemoteAdmin |
| `src/jenn_mesh/models/workbench.py` | Pydantic models for workbench + bulk push |
| `src/jenn_mesh/dashboard/routes/workbench.py` | 9 API endpoints (workbench + bulk push) |
| `src/jenn_mesh/dashboard/app.py` | FastAPI dashboard application factory |
| `src/jenn_mesh/cli.py` | CLI entry point with subcommands |
| `src/jenn_mesh/db.py` | SQLite WAL schema (devices, positions, alerts, configs) |
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
