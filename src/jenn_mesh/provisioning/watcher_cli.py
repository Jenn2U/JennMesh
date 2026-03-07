"""Radio watcher CLI — jenn-radio-watcher entrypoint."""

from __future__ import annotations

import logging
import signal
import sys

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for jenn-radio-watcher daemon."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="jenn-radio-watcher",
        description="JennMesh radio auto-provisioning daemon — watches for USB radios",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=None,
        help="Seconds between USB port scans (default: env JENN_RADIO_POLL_INTERVAL or 10)",
    )
    parser.add_argument(
        "--default-role",
        default=None,
        help="Default device role for new radios (default: env JENN_RADIO_DEFAULT_ROLE or CLIENT)",
    )
    parser.add_argument(
        "--auto-flash",
        action="store_true",
        default=None,
        help="Enable firmware erase+flash for unregistered radios",
    )
    parser.add_argument(
        "--no-auto-flash",
        action="store_true",
        default=False,
        help="Disable firmware flash (config-only provisioning)",
    )
    parser.add_argument(
        "--firmware-cache",
        default=None,
        help="Path to firmware cache directory",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to mesh SQLite database",
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

    logger.info("Starting JennMesh radio watcher daemon")

    # Build config from env, then override with CLI args
    from jenn_mesh.provisioning.radio_watcher import WatcherConfig

    config = WatcherConfig.from_env()
    if args.poll_interval is not None:
        config.poll_interval = args.poll_interval
    if args.default_role is not None:
        config.default_role = args.default_role
    if args.no_auto_flash:
        config.auto_flash = False
    elif args.auto_flash is not None:
        config.auto_flash = True
    if args.firmware_cache is not None:
        config.firmware_cache = args.firmware_cache
    if args.db_path is not None:
        config.db_path = args.db_path

    # Initialize components (lazy imports to avoid errors when deps are missing)
    from jenn_mesh.core.channel_manager import ChannelManager
    from jenn_mesh.core.config_manager import ConfigManager
    from jenn_mesh.db import MeshDatabase
    from jenn_mesh.provisioning.bench_flash import BenchProvisioner
    from jenn_mesh.provisioning.firmware import FirmwareTracker
    from jenn_mesh.provisioning.firmware_downloader import FirmwareDownloader
    from jenn_mesh.provisioning.flash_pipeline import FlashPipeline
    from jenn_mesh.provisioning.radio_watcher import RadioWatcher
    from jenn_mesh.provisioning.security import SecuritySetup

    db_path = config.db_path or "/var/lib/jenn-mesh/mesh.db"
    db = MeshDatabase(db_path)

    firmware_tracker = FirmwareTracker(db)
    firmware_tracker.seed_compatibility_matrix()

    downloader = FirmwareDownloader(config.firmware_cache or None)
    flash_pipeline = FlashPipeline(downloader)

    config_manager = ConfigManager(db)
    channel_manager = ChannelManager(db)
    security = SecuritySetup(db)

    bench = BenchProvisioner(
        db=db,
        config_manager=config_manager,
        channel_manager=channel_manager,
        security=security,
    )

    watcher = RadioWatcher(
        config=config,
        db=db,
        firmware_tracker=firmware_tracker,
        flash_pipeline=flash_pipeline,
        bench_provisioner=bench,
    )

    # Signal handling for clean shutdown
    def handle_signal(signum: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down", signum)
        watcher.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Run daemon
    try:
        watcher.run()
    except KeyboardInterrupt:
        watcher.stop()
    finally:
        logger.info("Radio watcher daemon stopped")


if __name__ == "__main__":
    main()
