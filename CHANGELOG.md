# Changelog

All notable changes to JennMesh will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
