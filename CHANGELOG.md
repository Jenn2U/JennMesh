# Changelog

All notable changes to JennMesh will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
