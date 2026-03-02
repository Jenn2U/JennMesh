# JennMesh

Meshtastic LoRa radio fleet management for the JENN Intelligent Ecosystem.

## Features

- **Fleet Management**: Device registry, health monitoring, offline alerts
- **Bench Provisioning**: USB detect + golden config flash (4 role templates)
- **Config Drift Detection**: Compare live radio configs against golden templates
- **Lost Node Locator**: GPS position tracking, proximity search, confidence levels
- **MQTT Telemetry**: Dedicated broker (port 1884) for mesh traffic ingestion
- **PKC Security**: Admin key management, Managed Mode, encrypted channels
- **Dashboard**: Fleet map, device list, config manager, alerts — Thinking Canvas UI

## Quick Start

```bash
# Install (dashboard)
pip install -e ".[dashboard]"

# Start dashboard
jenn-mesh serve
# → http://localhost:8002

# Install (CLI + provisioning)
pip install -e ".[cli]"

# Bench-provision a radio
jenn-mesh provision --role relay --port /dev/ttyUSB0

# Fleet health
jenn-mesh fleet list
jenn-mesh fleet health

# Config drift check
jenn-mesh config drift

# Locate a lost node
jenn-mesh locate !2A3B4C5D
```

## Golden Config Templates

Four role-based templates in `configs/`:

| Role | File | Use Case |
|------|------|----------|
| Relay | `relay-node.yaml` | Fixed repeater, no GPS, router mode |
| Gateway | `edge-gateway.yaml` | Edge node radio, MQTT relay, WiFi |
| Mobile | `mobile-client.yaml` | Field device, GPS on, BLE on |
| Sensor | `sensor-node.yaml` | Environment monitor, low power |

## Agent

Lightweight daemon for edge nodes with attached radios:

```bash
pip install -e ".[agent]"
jenn-mesh-agent --port /dev/ttyUSB0
```

Publishes telemetry to dedicated MQTT broker at `jenn/mesh/{region}/json/{channel}/{nodeId}`.

## Development

```bash
pip install -e ".[dev,test]"
pytest tests/ -v --tb=short
black src/ tests/
flake8 src/ tests/
mypy src/
```

## Infrastructure

- **Dashboard**: Port 8002 — `mesh.jenn2u.ai` (via Azure Front Door)
- **MQTT Broker**: Port 1884 — dedicated Mosquitto for mesh traffic
- **Container App**: `jennmesh-{env}` in `jenn-ai-rg`

## Architecture

JennMesh is standalone — it does not depend on JennEdge, Jenn Production, or JennSentry at runtime. Radios operate independently of edge nodes (relay stations, mountaintop repeaters, mobile field devices may have no JennEdge installation).
