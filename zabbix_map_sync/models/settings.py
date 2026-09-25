from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    netbox_url: str
    netbox_token: str
    netbox_topology_path: str
    netbox_topology_query: str
    netbox_required_tag: str
    netbox_ignored_device_roles: tuple[str, ...]
    zabbix_url: str
    zabbix_user: str
    zabbix_password: str
    zabbix_token: str
    zabbix_map_name: str
    zabbix_map_width: int
    zabbix_map_height: int
    zabbix_layout_grid_x: int
    zabbix_layout_grid_y: int
    zabbix_skipped_node_mode: str
    zabbix_skipped_node_icon_id: str
    zabbix_maps_config: str


@dataclass(frozen=True)
class MapDefinition:
    name: str
    topology_path: str
    topology_query: str
    width: int
    height: int
    grid_x: int
    grid_y: int
    required_tag: str
    ignored_device_roles: tuple[str, ...]
    skipped_node_mode: str
    skipped_node_icon_id: str
