from __future__ import annotations

import json
import os
import tempfile
import threading

from .models import MapDefinition, Settings


class ConfigurationError(ValueError):
    pass


VALID_SKIPPED_NODE_MODES = ("skip", "image")
DEFAULT_MAPS_CONFIG_PATH = "maps.json"

_maps_store_lock = threading.Lock()


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
        zabbix_maps_config=os.getenv("ZABBIX_MAPS_CONFIG", "").strip() or DEFAULT_MAPS_CONFIG_PATH,
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


def default_map_definition(settings: Settings) -> MapDefinition:
    """Map built purely from env vars; also the pre-filled values of the web form."""
    return MapDefinition(
        name=settings.zabbix_map_name,
        topology_path=settings.netbox_topology_path,
        topology_query=settings.netbox_topology_query,
        width=settings.zabbix_map_width,
        height=settings.zabbix_map_height,
        grid_x=settings.zabbix_layout_grid_x,
        grid_y=settings.zabbix_layout_grid_y,
        required_tag=settings.netbox_required_tag,
        ignored_device_roles=settings.netbox_ignored_device_roles,
        skipped_node_mode=settings.zabbix_skipped_node_mode,
        skipped_node_icon_id=settings.zabbix_skipped_node_icon_id,
    )


def _entry_int(entry: dict, key: str, default: int, name: str) -> int:
    value = entry.get(key, default)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"\"{key}\" for map {name} must be an integer, got: {value!r}") from exc


def _entry_str(entry: dict, key: str, default: str) -> str:
    value = entry.get(key)
    return default if value is None else str(value).strip()


def parse_map_definition(entry, defaults: MapDefinition) -> MapDefinition:
    """Validate one map entry (from the maps JSON or the web form); missing fields fall back to defaults."""
    if not isinstance(entry, dict):
        raise ConfigurationError(f"Each map definition must be an object, got: {entry!r}")

    name = str(entry.get("name", "")).strip()
    topology_query = entry.get("topology_query")
    if not name or topology_query is None:
        raise ConfigurationError(
            f"Each map definition requires \"name\" and \"topology_query\", got: {entry!r}"
        )

    ignored_roles = entry.get("ignored_device_roles")
    if isinstance(ignored_roles, str):
        ignored_roles = [item for item in (part.strip() for part in ignored_roles.split(",")) if item]
    if ignored_roles is not None and not isinstance(ignored_roles, list):
        raise ConfigurationError(f"\"ignored_device_roles\" for map {name} must be a list")

    skipped_node_mode = _entry_str(entry, "skipped_node_mode", defaults.skipped_node_mode).lower()
    if skipped_node_mode not in VALID_SKIPPED_NODE_MODES:
        raise ConfigurationError(
            f"\"skipped_node_mode\" for map {name} must be one of: " + ", ".join(VALID_SKIPPED_NODE_MODES)
        )

    return MapDefinition(
        name=name,
        topology_path=_entry_str(entry, "topology_path", defaults.topology_path) or defaults.topology_path,
        topology_query=str(topology_query).strip(),
        width=_entry_int(entry, "width", defaults.width, name),
        height=_entry_int(entry, "height", defaults.height, name),
        grid_x=_entry_int(entry, "grid_x", defaults.grid_x, name),
        grid_y=_entry_int(entry, "grid_y", defaults.grid_y, name),
        required_tag=_entry_str(entry, "required_tag", defaults.required_tag),
        ignored_device_roles=tuple(str(role).strip() for role in ignored_roles if str(role).strip())
        if ignored_roles is not None
        else defaults.ignored_device_roles,
        skipped_node_mode=skipped_node_mode,
        skipped_node_icon_id=_entry_str(entry, "skipped_node_icon_id", defaults.skipped_node_icon_id),
    )


def map_definition_to_dict(map_def: MapDefinition) -> dict:
    return {
        "name": map_def.name,
        "topology_path": map_def.topology_path,
        "topology_query": map_def.topology_query,
        "width": map_def.width,
        "height": map_def.height,
        "grid_x": map_def.grid_x,
        "grid_y": map_def.grid_y,
        "required_tag": map_def.required_tag,
        "ignored_device_roles": list(map_def.ignored_device_roles),
        "skipped_node_mode": map_def.skipped_node_mode,
        "skipped_node_icon_id": map_def.skipped_node_icon_id,
    }


def _read_maps_file(path: str) -> list | None:
    """Raw "maps" array, or None when the file does not exist yet."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigurationError(f"Could not read ZABBIX_MAPS_CONFIG file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON in ZABBIX_MAPS_CONFIG file {path}: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("maps"), list):
        raise ConfigurationError(f"ZABBIX_MAPS_CONFIG file {path} must contain a top-level \"maps\" array")
    return raw["maps"]


def load_saved_map_definitions(settings: Settings) -> list[MapDefinition]:
    raw_maps = _read_maps_file(settings.zabbix_maps_config) or []
    defaults = default_map_definition(settings)

    definitions: list[MapDefinition] = []
    seen_names: set[str] = set()
    for entry in raw_maps:
        map_def = parse_map_definition(entry, defaults)
        if map_def.name in seen_names:
            raise ConfigurationError(f"Duplicate map name in ZABBIX_MAPS_CONFIG: {map_def.name}")
        seen_names.add(map_def.name)
        definitions.append(map_def)
    return definitions


def load_map_definitions(settings: Settings) -> list[MapDefinition]:
    """Maps that /sync and /webhook synchronize.

    Saved maps win; with no saved maps yet, the single env-defined map is used
    so a deployment configured only through env vars keeps working.
    """
    return load_saved_map_definitions(settings) or [default_map_definition(settings)]


def _write_maps_file(path: str, definitions: list[MapDefinition]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    body = {"maps": [map_definition_to_dict(map_def) for map_def in definitions]}
    # Write-then-rename so a crash mid-write never leaves a truncated maps file.
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".maps-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def save_map_definition(settings: Settings, map_def: MapDefinition, previous_name: str | None = None) -> None:
    """Insert or replace a map in the maps JSON file.

    An existing entry named ``map_def.name`` -- or ``previous_name`` when the
    map is being renamed -- is replaced in place; otherwise the map is appended.
    """
    replaced_names = {map_def.name, previous_name} - {None}
    with _maps_store_lock:
        definitions: list[MapDefinition] = []
        inserted = False
        for item in load_saved_map_definitions(settings):
            if item.name not in replaced_names:
                definitions.append(item)
            elif not inserted:
                definitions.append(map_def)
                inserted = True
        if not inserted:
            definitions.append(map_def)
        _write_maps_file(settings.zabbix_maps_config, definitions)


def delete_map_definition(settings: Settings, name: str) -> bool:
    """Remove a map from the maps JSON file; the Zabbix map itself is left untouched."""
    with _maps_store_lock:
        definitions = load_saved_map_definitions(settings)
        remaining = [item for item in definitions if item.name != name]
        if len(remaining) == len(definitions):
            return False
        _write_maps_file(settings.zabbix_maps_config, remaining)
        return True
