"""Agent daemon CLI — jenn-mesh-agent entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for jenn-mesh-agent daemon."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="jenn-mesh-agent",
        description="JennMesh agent daemon — bridges local radio to MQTT",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port for radio (auto-detect if omitted)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="TCP host:port for radio (e.g., 10.10.50.100:4403)",
    )
    parser.add_argument(
        "--mqtt-broker",
        default="mqtt.jenn2u.ai",
        help="MQTT broker address",
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=1884,
        help="MQTT broker port",
    )
    parser.add_argument(
        "--mqtt-username",
        default="jenn-mesh",
        help="MQTT username",
    )
    parser.add_argument(
        "--mqtt-password",
        default=None,
        help="MQTT password",
    )
    parser.add_argument(
        "--dashboard-url",
        default=None,
        help="Dashboard URL for health reporting",
    )
    parser.add_argument(
        "--region",
        default="US",
        help="LoRa region (used in MQTT topic namespace)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=120,
        help="Mesh heartbeat interval in seconds (default: 120)",
    )
    parser.add_argument(
        "--heartbeat-disable",
        action="store_true",
        help="Disable mesh heartbeat sending",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Starting JennMesh agent daemon")

    # Import here to avoid import errors when meshtastic isn't installed
    from jenn_mesh.agent.health import AgentHealthMonitor
    from jenn_mesh.agent.heartbeat_sender import HeartbeatSender
    from jenn_mesh.agent.radio_bridge import (
        PACKET_NODEINFO,
        PACKET_POSITION,
        PACKET_TELEMETRY,
        PACKET_TEXT,
        RadioBridge,
    )

    # Initialize components
    bridge = RadioBridge(port=args.port, host=args.host)
    health = AgentHealthMonitor()
    mqtt_client = _create_mqtt_client(args)

    # Initialize heartbeat sender (disabled with --heartbeat-disable)
    heartbeat_sender: Optional[HeartbeatSender] = None
    if not args.heartbeat_disable:
        # Node ID will be determined after radio connection
        heartbeat_sender = HeartbeatSender(
            node_id="",  # Placeholder — set after connection
            bridge=bridge,
            interval=args.heartbeat_interval,
        )
        logger.info("Mesh heartbeat enabled (interval=%ds)", args.heartbeat_interval)

    # Wire up packet forwarding: radio -> MQTT
    def forward_to_mqtt(packet: dict) -> None:
        health.record_packet_received()
        topic = _build_topic(args.region, packet)
        try:
            if mqtt_client:
                mqtt_client.publish(topic, json.dumps(packet["payload"]))
                health.record_packet_forwarded()
        except Exception as e:
            logger.error("MQTT publish error: %s", e)

    bridge.on_packet(PACKET_NODEINFO, forward_to_mqtt)
    bridge.on_packet(PACKET_POSITION, forward_to_mqtt)
    bridge.on_packet(PACKET_TELEMETRY, forward_to_mqtt)
    bridge.on_packet(PACKET_TEXT, forward_to_mqtt)

    # Connect
    if not bridge.connect():
        logger.error("Failed to connect to radio, exiting")
        sys.exit(1)

    health.set_radio_status(True, port=args.port or args.host or "auto")

    # Set heartbeat sender's node_id from connected radio
    if heartbeat_sender:
        try:
            nodes = bridge.get_node_info()
            if nodes:
                # First node key is typically our own node
                local_node_id = next(iter(nodes), "")
                if local_node_id:
                    heartbeat_sender.node_id = local_node_id
                    logger.info("Heartbeat sender node_id: %s", local_node_id)
        except Exception:
            logger.warning("Could not determine local node_id for heartbeat")

    if mqtt_client:
        try:
            mqtt_client.connect(args.mqtt_broker, args.mqtt_port)
            mqtt_client.loop_start()
            health.set_mqtt_status(True)
            logger.info("MQTT connected to %s:%d", args.mqtt_broker, args.mqtt_port)
        except Exception as e:
            logger.error("MQTT connection failed: %s", e)

    # Signal handling for clean shutdown
    running = True

    def handle_signal(signum: int, frame: object) -> None:
        nonlocal running
        logger.info("Received signal %d, shutting down", signum)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Main loop
    logger.info("Agent daemon running — forwarding mesh packets to MQTT")
    health_interval = 60  # Report health every 60 seconds
    last_health = time.time()

    try:
        while running:
            time.sleep(1)

            # Periodic health reporting
            if args.dashboard_url and (time.time() - last_health) >= health_interval:
                last_health = time.time()
                try:
                    asyncio.run(health.report_to_dashboard(args.dashboard_url))
                except Exception:
                    pass

            # Periodic mesh heartbeat
            if heartbeat_sender and heartbeat_sender.node_id:
                report = health.get_report()
                services = heartbeat_sender.build_services_from_health(report)
                heartbeat_sender.maybe_send(
                    uptime_seconds=int(report.uptime_seconds),
                    services=services,
                    battery=-1,
                )
    finally:
        bridge.disconnect()
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        logger.info("Agent daemon stopped")


def _create_mqtt_client(args: object) -> Optional[object]:
    """Create a paho MQTT client if available."""
    try:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="jenn-mesh-agent",
        )
        if hasattr(args, "mqtt_username") and args.mqtt_username:
            client.username_pw_set(args.mqtt_username, args.mqtt_password or "")
        return client
    except ImportError:
        logger.warning("paho-mqtt not installed, MQTT forwarding disabled")
        return None


def _build_topic(region: str, packet: dict) -> str:
    """Build MQTT topic from packet metadata."""
    ptype = packet.get("type", "unknown")
    from_id = packet.get("from", "unknown")
    return f"jenn/mesh/{region}/json/{ptype}/{from_id}"


if __name__ == "__main__":
    main()
