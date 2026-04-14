from __future__ import annotations

import logging
from dataclasses import dataclass

from zabbix_map_sync.models import TopologyGraph
from zabbix_map_sync.sync import SyncResult

from .config import Settings, load_settings
from .netbox import NetBoxClient
from .sync import sync_topology_to_zabbix_map
from .zabbix import ZabbixClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DryRunResult:
    total_nodes: int
    total_links: int


def run_synchronization(dry_run: bool = False) -> SyncResult | DryRunResult:
    settings: Settings = load_settings()
    print(
        "[zbx-map-sync] synchronization start "
        f"dry_run={dry_run} path={settings.netbox_topology_path} map={settings.zabbix_map_name}",
        flush=True,
    )
    logger.info(
        "Starting synchronization dry_run=%s topology_path=%s required_tag=%s ignored_roles=%s map_name=%s",
        dry_run,
        settings.netbox_topology_path,
        settings.netbox_required_tag or "<none>",
        ",".join(settings.netbox_ignored_device_roles) or "<none>",
        settings.zabbix_map_name,
    )

    netbox = NetBoxClient(
        base_url=settings.netbox_url,
        token=settings.netbox_token,
        required_tag=settings.netbox_required_tag,
        ignored_device_roles=settings.netbox_ignored_device_roles,
    )
    topology: TopologyGraph = netbox.fetch_topology(
        path=settings.netbox_topology_path,
        query=settings.netbox_topology_query,
    )
    logger.info(
        "Fetched topology nodes=%s edges=%s",
        len(topology.nodes),
        len(topology.edges),
    )

    zabbix = ZabbixClient(
        api_url=settings.zabbix_url,
        user=settings.zabbix_user,
        password=settings.zabbix_password,
        api_token=settings.zabbix_token,
    )
    zabbix.login()
    logger.debug("Authenticated to Zabbix API")

    if dry_run:
        logger.info("Dry-run completed without applying map changes")
        print(
            f"[zbx-map-sync] dry-run done nodes={len(topology.nodes)} edges={len(topology.edges)}",
            flush=True,
        )
        return DryRunResult(total_nodes=len(topology.nodes), total_links=len(topology.edges))

    result = sync_topology_to_zabbix_map(
        graph=topology,
        zabbix=zabbix,
        map_name=settings.zabbix_map_name,
        width=settings.zabbix_map_width,
        height=settings.zabbix_map_height,
        grid_x=settings.zabbix_layout_grid_x,
        grid_y=settings.zabbix_layout_grid_y,
    )
    logger.info(
        "Synchronization finished created=%s matched_hosts=%s total_links=%s unresolved_link_rules=%s",
        result.created,
        result.matched_hosts,
        result.total_links,
        result.unresolved_link_rules,
    )
    print(
        "[zbx-map-sync] synchronization done "
        f"created={result.created} matched_hosts={result.matched_hosts} "
        f"links={result.total_links} unresolved={result.unresolved_link_rules}",
        flush=True,
    )
    return result
