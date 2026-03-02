"""JennMesh CLI — main entrypoint for fleet management commands."""

from __future__ import annotations

import sys


def main() -> None:
    """Main CLI entrypoint: jenn-mesh <command> [options]."""
    try:
        import click
    except ImportError:
        print("CLI requires: pip install 'jenn-mesh[cli]'")
        sys.exit(1)

    from jenn_mesh.db import MeshDatabase

    @click.group()
    @click.option("--db-path", default=None, help="Path to SQLite database")
    @click.pass_context
    def cli(ctx: click.Context, db_path: str | None) -> None:
        """JennMesh — Meshtastic fleet management CLI."""
        ctx.ensure_object(dict)
        ctx.obj["db"] = MeshDatabase(db_path)

    # ── Fleet commands ──────────────────────────────────────────────

    @cli.group()
    @click.pass_context
    def fleet(ctx: click.Context) -> None:
        """Fleet management commands."""
        pass

    @fleet.command("list")
    @click.pass_context
    def fleet_list(ctx: click.Context) -> None:
        """List all known devices in the fleet."""
        from rich.console import Console
        from rich.table import Table

        db = ctx.obj["db"]
        devices = db.list_devices()
        console = Console()

        table = Table(title="JennMesh Fleet Registry")
        table.add_column("Node ID", style="cyan")
        table.add_column("Name", style="white")
        table.add_column("Role", style="green")
        table.add_column("Hardware", style="yellow")
        table.add_column("Firmware", style="yellow")
        table.add_column("Battery", style="magenta")
        table.add_column("Last Seen", style="blue")

        for d in devices:
            battery = f"{d['battery_level']}%" if d.get("battery_level") else "—"
            table.add_row(
                d["node_id"],
                d.get("long_name", ""),
                d.get("role", "CLIENT"),
                d.get("hw_model", "unknown"),
                d.get("firmware_version", "unknown"),
                battery,
                d.get("last_seen", "never"),
            )

        console.print(table)

    @fleet.command("health")
    @click.pass_context
    def fleet_health(ctx: click.Context) -> None:
        """Show fleet health summary."""
        from rich.console import Console
        from rich.panel import Panel

        from jenn_mesh.core.registry import DeviceRegistry

        db = ctx.obj["db"]
        registry = DeviceRegistry(db)
        health = registry.get_fleet_health()
        console = Console()

        status = (
            f"[bold]Total Devices:[/bold] {health.total_devices}\n"
            f"[green]Online:[/green] {health.online_count}  "
            f"[red]Offline:[/red] {health.offline_count}  "
            f"[yellow]Degraded:[/yellow] {health.degraded_count}\n"
            f"[bold]Health Score:[/bold] {health.health_score:.1f}%\n"
            f"Active Alerts: {health.active_alerts} "
            f"([red]Critical: {health.critical_alerts}[/red])\n"
            f"Need Update: {health.devices_needing_update}  "
            f"Config Drift: {health.devices_with_drift}"
        )
        console.print(Panel(status, title="Fleet Health", border_style="cyan"))

    # ── Config commands ─────────────────────────────────────────────

    @cli.group()
    @click.pass_context
    def config(ctx: click.Context) -> None:
        """Configuration management commands."""
        pass

    @config.command("drift")
    @click.pass_context
    def config_drift(ctx: click.Context) -> None:
        """Check for configuration drift across the fleet."""
        from rich.console import Console
        from rich.table import Table

        from jenn_mesh.core.config_manager import ConfigManager

        db = ctx.obj["db"]
        cm = ConfigManager(db)
        drifted = cm.get_drift_report()
        console = Console()

        if not drifted:
            console.print("[green]No configuration drift detected.[/green]")
            return

        table = Table(title="Config Drift Report")
        table.add_column("Node ID", style="cyan")
        table.add_column("Name", style="white")
        table.add_column("Role", style="yellow")
        table.add_column("Device Hash", style="red")
        table.add_column("Template Hash", style="green")

        for d in drifted:
            table.add_row(
                d["node_id"],
                d["long_name"],
                d["role"],
                d["device_hash"][:12] + "...",
                d["template_hash"][:12] + "...",
            )
        console.print(table)

    @config.command("load-templates")
    @click.pass_context
    def config_load(ctx: click.Context) -> None:
        """Load golden config templates from disk into the database."""
        from rich.console import Console

        from jenn_mesh.core.config_manager import ConfigManager

        db = ctx.obj["db"]
        cm = ConfigManager(db)
        templates = cm.load_templates_from_disk()
        console = Console()

        for role in templates:
            console.print(f"  [green]Loaded:[/green] {role}")
        console.print(f"\n[bold]{len(templates)} templates loaded.[/bold]")

    # ── Provision commands ──────────────────────────────────────────

    @cli.command()
    @click.option(
        "--role",
        type=click.Choice(["relay", "gateway", "mobile", "sensor"]),
        prompt="Device role",
        help="Device role for provisioning",
    )
    @click.option("--port", default=None, help="Serial port (auto-detect if omitted)")
    @click.option("--name", default=None, help="Device long name")
    @click.option("--short-name", default=None, help="4-char short name")
    @click.pass_context
    def provision(
        ctx: click.Context,
        role: str,
        port: str | None,
        name: str | None,
        short_name: str | None,
    ) -> None:
        """Provision a connected Meshtastic radio with a golden config."""
        from rich.console import Console

        from jenn_mesh.core.channel_manager import ChannelManager
        from jenn_mesh.core.config_manager import ConfigManager
        from jenn_mesh.models.device import DeviceRole
        from jenn_mesh.provisioning.bench_flash import BenchProvisioner
        from jenn_mesh.provisioning.security import SecuritySetup

        db = ctx.obj["db"]
        console = Console()

        role_map = {
            "relay": DeviceRole.RELAY,
            "gateway": DeviceRole.GATEWAY,
            "mobile": DeviceRole.MOBILE,
            "sensor": DeviceRole.SENSOR,
        }

        provisioner = BenchProvisioner(
            db=db,
            config_manager=ConfigManager(db),
            channel_manager=ChannelManager(db),
            security=SecuritySetup(),
        )

        serial_port = port or provisioner.detect_serial_port()
        if serial_port is None:
            console.print("[red]No Meshtastic radio detected.[/red]")
            return

        console.print(f"Provisioning as [bold]{role}[/bold] on {serial_port}...")
        result = provisioner.apply_golden_config(
            role=role_map[role],
            port=serial_port,
            long_name=name,
            short_name=short_name,
        )

        if result.success:
            console.print(f"[green]Success![/green] {result.message}")
            console.print(f"  Node ID: {result.node_id}")
            console.print(f"  Config hash: {result.config_hash[:12]}...")
        else:
            console.print(f"[red]Failed:[/red] {result.message}")

    # ── Locate commands ─────────────────────────────────────────────

    @cli.command()
    @click.argument("node_id")
    @click.option("--radius", default=5000.0, help="Search radius in meters")
    @click.pass_context
    def locate(ctx: click.Context, node_id: str, radius: float) -> None:
        """Locate a lost mesh node or edge device."""
        from rich.console import Console
        from rich.panel import Panel

        from jenn_mesh.locator.finder import LostNodeFinder
        from jenn_mesh.models.location import LostNodeQuery

        db = ctx.obj["db"]
        finder = LostNodeFinder(db)
        console = Console()

        result = finder.locate(
            LostNodeQuery(target_node_id=node_id, search_radius_meters=radius)
        )

        if not result.is_found:
            console.print(f"[red]No position data for {node_id}[/red]")
            return

        pos = result.last_known_position
        info = (
            f"[bold]Target:[/bold] {result.target_node_id}\n"
            f"[bold]Position:[/bold] {pos.latitude:.6f}, {pos.longitude:.6f}\n"
            f"[bold]Age:[/bold] {result.position_age_hours:.1f} hours\n"
            f"[bold]Confidence:[/bold] {result.confidence}\n"
            f"[bold]Nearby Nodes:[/bold] {len(result.nearby_nodes)}"
        )
        if result.associated_edge_node:
            info += f"\n[bold]Edge Node:[/bold] {result.associated_edge_node}"

        console.print(Panel(info, title="Lost Node Locator", border_style="cyan"))

        if result.nearby_nodes:
            from rich.table import Table

            table = Table(title="Nearby Active Nodes")
            table.add_column("Node ID", style="cyan")
            table.add_column("Distance", style="yellow")
            table.add_column("Online", style="green")

            for n in result.nearby_nodes[:10]:
                table.add_row(
                    n.node_id,
                    f"{n.distance_meters:.0f}m",
                    "[green]yes[/green]" if n.is_online else "[red]no[/red]",
                )
            console.print(table)

    # ── Serve command ───────────────────────────────────────────────

    @cli.command()
    @click.option("--host", default="0.0.0.0", help="Dashboard host")
    @click.option("--port", default=8002, help="Dashboard port")
    @click.pass_context
    def serve(ctx: click.Context, host: str, port: int) -> None:
        """Start the JennMesh dashboard."""
        import uvicorn

        from jenn_mesh.dashboard.app import create_app

        db = ctx.obj["db"]
        app = create_app(db)
        uvicorn.run(app, host=host, port=port)

    # ── Agent command ───────────────────────────────────────────────

    @cli.command()
    @click.option("--radio-port", default=None, help="Serial port for radio")
    @click.option("--mqtt-broker", default="mqtt.jenn2u.ai")
    @click.option("--mqtt-port", default=1884, type=int)
    @click.pass_context
    def agent(ctx: click.Context, radio_port: str | None, mqtt_broker: str, mqtt_port: int) -> None:
        """Start the JennMesh agent daemon."""
        from jenn_mesh.agent.cli import main as agent_main

        # Delegate to the agent CLI
        sys.argv = ["jenn-mesh-agent"]
        if radio_port:
            sys.argv.extend(["--port", radio_port])
        sys.argv.extend(["--mqtt-broker", mqtt_broker, "--mqtt-port", str(mqtt_port)])
        agent_main()

    cli()


if __name__ == "__main__":
    main()
