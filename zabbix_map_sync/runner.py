from __future__ import annotations

import logging

from .config import load_map_definitions, load_settings
from .models import DryRunResult, MapDefinition, MapSyncError, Settings, SyncResult, TopologyGraph
from .netbox import NetBoxClient
from .sync import sync_topology_to_zabbix_map
from .zabbix import ZabbixClient


logger = logging.getLogger(__name__)


def _sync_one_map(
    settings: Settings,
    map_def: MapDefinition,
    zabbix: ZabbixClient,
    dry_run: bool,
) -> SyncResult | DryRunResult:
    logger.info(
        "Starting synchronization for map=%s dry_run=%s topology_path=%s required_tag=%s ignored_roles=%s",
        map_def.name,
        dry_run,
        map_def.topology_path,
        map_def.required_tag or "<none>",
        ",".join(map_def.ignored_device_roles) or "<none>",
    )

    netbox = NetBoxClient(
        base_url=settings.netbox_url,
        token=settings.netbox_token,
        required_tag=map_def.required_tag,
        ignored_device_roles=map_def.ignored_device_roles,
    )
    topology: TopologyGraph = netbox.fetch_topology(
        path=map_def.topology_path,
        query=map_def.topology_query,
    )
    logger.info(
        "Fetched topology for map=%s nodes=%s edges=%s",
        map_def.name,
        len(topology.nodes),
        len(topology.edges),
    )

    if dry_run:
        logger.info("Dry-run completed without applying map changes map=%s", map_def.name)
        return DryRunResult(
            map_name=map_def.name,
            total_nodes=len(topology.nodes),
            total_links=len(topology.edges),
        )

    result = sync_topology_to_zabbix_map(
        graph=topology,
        zabbix=zabbix,
        netbox=netbox,
        map_name=map_def.name,
        width=map_def.width,
        height=map_def.height,
        grid_x=map_def.grid_x,
        grid_y=map_def.grid_y,
        skipped_node_mode=map_def.skipped_node_mode,
        skipped_node_icon_id=map_def.skipped_node_icon_id,
        icon_map=map_def.icon_map,
        inventory_role_sync=settings.zabbix_inventory_role_sync,
    )
    logger.info(
        "Synchronization finished map=%s created=%s matched_hosts=%s total_links=%s unresolved_link_rules=%s",
        map_def.name,
        result.created,
        result.matched_hosts,
        result.total_links,
        result.unresolved_link_rules,
    )
    return result


def _zabbix_client(settings: Settings) -> ZabbixClient:
    zabbix = ZabbixClient(
        api_url=settings.zabbix_url,
        user=settings.zabbix_user,
        password=settings.zabbix_password,
        api_token=settings.zabbix_token,
    )
    zabbix.login()
    logger.debug("Authenticated to Zabbix API")
    return zabbix


def sync_maps(
    settings: Settings,
    map_definitions: list[MapDefinition],
    dry_run: bool = False,
) -> list[SyncResult | DryRunResult | MapSyncError]:
    print(
        "[zbx-map-sync] synchronization start "
        f"dry_run={dry_run} maps={[m.name for m in map_definitions]}",
        flush=True,
    )
    zabbix = _zabbix_client(settings)

    results: list[SyncResult | DryRunResult | MapSyncError] = []
    for map_def in map_definitions:
        try:
            results.append(_sync_one_map(settings, map_def, zabbix, dry_run))
        except Exception as exc:
            logger.exception("Sync failed for map=%s", map_def.name)
            print(f"[zbx-map-sync] map={map_def.name} failed error={exc}", flush=True)
            results.append(MapSyncError(map_name=map_def.name, error=str(exc)))

    print(
        "[zbx-map-sync] synchronization done "
        f"maps={len(results)} failed={sum(1 for r in results if isinstance(r, MapSyncError))}",
        flush=True,
    )
    return results


def run_synchronization(dry_run: bool = False) -> list[SyncResult | DryRunResult | MapSyncError]:
    """Sync every configured map (saved maps, or the env-defined map when none are saved)."""
    settings: Settings = load_settings()
    return sync_maps(settings, load_map_definitions(settings), dry_run=dry_run)
