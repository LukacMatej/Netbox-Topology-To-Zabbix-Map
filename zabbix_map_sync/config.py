from __future__ import annotations

import os
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


class ConfigurationError(ValueError):
    pass


VALID_SKIPPED_NODE_MODES = ("skip", "image")


def _read_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value


def _read_csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values: list[str] = []
    for item in raw.split(","):
        text = item.strip()
        if text and text not in values:
            values.append(text)
    return tuple(values)


def load_settings() -> Settings:
    settings = Settings(
        netbox_url=_read_required("NETBOX_URL").rstrip("/"),
        netbox_token=_read_required("NETBOX_TOKEN"),
        netbox_topology_path=os.getenv(
            "NETBOX_TOPOLOGY_PATH", "/api/plugins/netbox_topology_views/xml-export/"
        ).strip(),
        netbox_topology_query=os.getenv("NETBOX_TOPOLOGY_QUERY", "").strip(),
        netbox_required_tag=os.getenv("NETBOX_REQUIRED_TAG", "").strip(),
        netbox_ignored_device_roles=_read_csv_env("NETBOX_IGNORED_DEVICE_ROLES"),
        zabbix_url=_read_required("ZABBIX_URL"),
        zabbix_user=os.getenv("ZABBIX_USER", "").strip(),
        zabbix_password=os.getenv("ZABBIX_PASSWORD", "").strip(),
        zabbix_token=os.getenv("ZABBIX_TOKEN", "").strip(),
        zabbix_map_name=os.getenv("ZABBIX_MAP_NAME", "NetBox Topology").strip()
        or "NetBox Topology",
        zabbix_map_width=int(os.getenv("ZABBIX_MAP_WIDTH", "1920")),
        zabbix_map_height=int(os.getenv("ZABBIX_MAP_HEIGHT", "1200")),
        zabbix_layout_grid_x=int(os.getenv("ZABBIX_LAYOUT_GRID_X", "40")),
        zabbix_layout_grid_y=int(os.getenv("ZABBIX_LAYOUT_GRID_Y", "40")),
        zabbix_skipped_node_mode=os.getenv("ZABBIX_SKIPPED_NODE_MODE", "skip").strip().lower()
        or "skip",
        zabbix_skipped_node_icon_id=os.getenv("ZABBIX_SKIPPED_NODE_ICON_ID", "").strip(),
    )
    if not settings.zabbix_token and not (settings.zabbix_user and settings.zabbix_password):
        raise ConfigurationError(
            "Provide ZABBIX_TOKEN or both ZABBIX_USER and ZABBIX_PASSWORD"
        )
    if settings.zabbix_skipped_node_mode not in VALID_SKIPPED_NODE_MODES:
        raise ConfigurationError(
            "ZABBIX_SKIPPED_NODE_MODE must be one of: "
            + ", ".join(VALID_SKIPPED_NODE_MODES)
        )
    return settings
