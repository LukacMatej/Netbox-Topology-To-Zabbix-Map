from __future__ import annotations

import argparse
import sys

import requests

from zabbix_map_sync.sync import SyncResult

from .config import ConfigurationError
from .logging_utils import configure_logging
from .runner import DryRunResult, run_synchronization
from .web import run_server
from .zabbix import ZabbixAPIError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update a Zabbix map from NetBox topology data"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse data but do not modify the Zabbix map",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run HTTP server with GET /sync and POST /webhook endpoints",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface for --serve mode (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for --serve mode (default: 8080)",
    )
    parser.add_argument(
        "--log-level",
        default="DEBUG",
        help="Logging level (default: DEBUG)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(getattr(args, "log_level", "DEBUG"))
    try:
        if getattr(args, "serve", False):
            run_server(host=args.host, port=args.port)
            return 0

        result = run_synchronization(dry_run=getattr(args, "dry_run", False))

        if isinstance(result, DryRunResult):
            print(f"Topology nodes: {result.total_nodes}")
            print(f"Topology links: {result.total_links}")
            print("Dry-run complete: no map changes applied")
            return 0

        sync_result: SyncResult = result
        action = "created" if sync_result.created else "updated"
        print(
            f"Map '{sync_result.map_name}' {action}. "
            f"Nodes: {sync_result.total_nodes}, matched hosts: {sync_result.matched_hosts}, "
            f"image nodes: {sync_result.image_nodes}, skipped nodes: {sync_result.skipped_nodes}, "
            f"links: {sync_result.total_links}"
        )
        if sync_result.unresolved_link_rules:
            print(
                f"Warning: {sync_result.unresolved_link_rules} cable trigger(s) from NetBox custom field "
                "'zabbix_triggers' could not find matching Zabbix triggers."
            )
            for detail in sync_result.unresolved_link_rule_details:
                print(f"  - {detail}")
        return 0

    except (ConfigurationError, ZabbixAPIError, ValueError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
