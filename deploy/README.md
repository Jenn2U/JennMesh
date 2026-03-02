# JennMesh Mesh Appliance — Deployment Guide

Deploy JennMesh as an all-in-one "mesh appliance" on ARM64 Linux for physical radio administration via USB or Bluetooth.

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Board | Raspberry Pi 5 (4GB) | Raspberry Pi 5 (8GB) or Orange Pi 5 |
| Storage | 32GB microSD | 64GB+ microSD or NVMe |
| USB | 1x USB-A port (for Meshtastic radio) | USB hub for multiple radios |
| Network | Ethernet or WiFi | Ethernet (more reliable) |
| Power | Official PSU (5V/5A for Pi 5) | UPS recommended for field use |

## OS Setup

1. Flash **Raspberry Pi OS Lite (64-bit)** or **Armbian** to microSD
2. Boot and complete initial setup (user, locale, timezone, SSH)
3. Install Python 3.11+ if not already present:
   ```bash
   sudo apt-get update
   sudo apt-get install -y python3.12 python3.12-venv sqlite3 curl
   ```
4. Plug in Meshtastic radio via USB

## First-Time Install

```bash
# Transfer the release tarball to the server
scp jenn-mesh-0.2.0-arm64.tar.gz user@meshbox-01:/tmp/

# SSH into the server
ssh user@meshbox-01

# Run the installer
sudo bash /tmp/install.sh \
  --version 0.2.0 \
  --tarball /tmp/jenn-mesh-0.2.0-arm64.tar.gz \
  --subnet 10.10.50.0/24
```

The installer will:
1. Create `jenn-mesh` system user with `dialout` group access
2. Install the application to `/opt/jenn-mesh/0.2.0/`
3. Install and configure Mosquitto MQTT broker
4. Set up udev rules for Meshtastic USB devices
5. Install and start 4 systemd services
6. Configure UFW firewall for LAN-only access
7. Run health checks

## Service Management

```bash
# View all JennMesh services
sudo systemctl status 'jenn-mesh-*' 'jenn-sentry-agent'

# Restart a specific service
sudo systemctl restart jenn-mesh-dashboard

# View logs (live)
sudo journalctl -u jenn-mesh-agent -f

# View all JennMesh logs from last hour
sudo journalctl -u 'jenn-mesh-*' --since '1 hour ago'

# Check health
sudo /opt/jenn-mesh/current/deploy/scripts/health-check.sh --verbose
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| `jenn-mesh-broker` | 1884 | Mosquitto MQTT broker |
| `jenn-mesh-dashboard` | 8002 | Web dashboard (FastAPI) |
| `jenn-mesh-agent` | — | Radio bridge → MQTT forwarder |
| `jenn-sentry-agent` | — | Health monitoring sidecar |

## Accessing the Dashboard

From any machine on the LAN:
```
http://<meshbox-ip>:8002
```

Example: `http://10.10.50.50:8002`

## USB Radio Connection

1. Plug a Meshtastic radio into any USB port
2. Verify it appears: `ls -la /dev/meshtastic*`
3. The agent service auto-detects the radio
4. Check agent logs: `sudo journalctl -u jenn-mesh-agent -f`

### Supported USB Chips

| Chip | Vendor:Product | Common Boards |
|------|---------------|---------------|
| CP2102 (Silicon Labs) | 10C4:EA60 | Heltec, LILYGO T-Beam |
| CH9102/CH340 (WCH) | 1A86:* | Budget clone boards |
| FTDI FT232 | 0403:6015 | Dev boards |

### Troubleshooting USB

```bash
# Check if radio is detected at kernel level
dmesg | tail -20

# Check udev rules are loaded
udevadm info /dev/ttyUSB0

# Manually reload udev rules
sudo udevadm control --reload-rules && sudo udevadm trigger

# Verify dialout group
groups jenn-mesh
```

## Configuration

Configuration files live in `/etc/jenn-mesh/`:

| File | Purpose |
|------|---------|
| `env` | Environment variables for all services |
| `mosquitto.conf` | MQTT broker configuration |
| `mosquitto_passwd` | MQTT authentication (generated on install) |

To edit configuration:
```bash
sudo nano /etc/jenn-mesh/env
sudo systemctl restart jenn-mesh-dashboard jenn-mesh-agent
```

## Version Upgrade

```bash
# Transfer new release
scp jenn-mesh-0.3.0-arm64.tar.gz user@meshbox-01:/tmp/

# Run installer (handles version swap automatically)
ssh user@meshbox-01 sudo bash /opt/jenn-mesh/current/deploy/scripts/install.sh \
  --version 0.3.0 \
  --tarball /tmp/jenn-mesh-0.3.0-arm64.tar.gz
```

The installer preserves your existing `/etc/jenn-mesh/env` configuration and keeps the previous version for rollback.

### Rollback

```bash
# Point current back to previous version
sudo ln -sfn /opt/jenn-mesh/previous /opt/jenn-mesh/current
sudo systemctl restart jenn-mesh-broker jenn-mesh-dashboard jenn-mesh-agent jenn-sentry-agent
```

## Backup & Restore

### Automatic Backups

Nightly SQLite backups run at 02:00 via cron:
- Location: `/var/lib/jenn-mesh/backups/`
- Retention: 14 days
- Format: `mesh-YYYYMMDD-HHMMSS.db.gz`

### Manual Backup

```bash
sudo -u jenn-mesh /opt/jenn-mesh/current/deploy/scripts/backup-mesh-db.sh
```

### Restore

```bash
sudo systemctl stop jenn-mesh-dashboard jenn-mesh-agent
gunzip -c /var/lib/jenn-mesh/backups/mesh-20260302-020000.db.gz > /var/lib/jenn-mesh/mesh.db
sudo chown jenn-mesh:jenn-mesh /var/lib/jenn-mesh/mesh.db
sudo systemctl start jenn-mesh-dashboard jenn-mesh-agent
```

## Troubleshooting

### Dashboard unreachable

```bash
# Check service status
sudo systemctl status jenn-mesh-dashboard

# Check if port is in use
ss -tlnp | grep 8002

# Check firewall
sudo ufw status
```

### MQTT connection refused

```bash
# Check broker is running
sudo systemctl status jenn-mesh-broker

# Test local connection
mosquitto_sub -h 127.0.0.1 -p 1884 -u jenn-mesh -P <password> -t '#' -v

# Check broker logs
sudo journalctl -u jenn-mesh-broker --since '10 min ago'
```

### Agent not connecting to radio

```bash
# Check USB device
ls -la /dev/meshtastic* /dev/ttyUSB* /dev/ttyACM*

# Check agent logs
sudo journalctl -u jenn-mesh-agent -f

# Check permissions
id jenn-mesh  # Should include 'dialout' group
```

## Directory Structure

```
/opt/jenn-mesh/
├── current -> 0.2.0/           # Active version (symlink)
├── 0.2.0/                      # Versioned install
│   ├── venv/                   # Python virtualenv
│   ├── src/                    # Package source
│   ├── configs/                # Golden radio templates
│   └── deploy/                 # Deploy scripts + configs
├── previous -> 0.1.0/          # Rollback target
/etc/jenn-mesh/
├── env                         # Environment variables
├── mosquitto.conf              # Broker config
├── mosquitto_passwd            # Broker auth
/var/lib/jenn-mesh/
├── mesh.db                     # SQLite database
├── mosquitto/                  # Broker persistence
├── backups/                    # Nightly DB backups
/var/log/jenn-mesh/             # Service logs
```
