import pytest

from zabbix_map_sync.config import (
    ConfigurationError,
    default_map_definition,
    delete_map_definition,
    load_map_definitions,
    load_settings,
    parse_map_definition,
    save_map_definition,
)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "NETBOX_URL",
        "NETBOX_TOKEN",
        "NETBOX_TOPOLOGY_PATH",
        "NETBOX_TOPOLOGY_QUERY",
        "NETBOX_REQUIRED_TAG",
        "NETBOX_IGNORED_DEVICE_ROLES",
        "ZABBIX_URL",
        "ZABBIX_USER",
        "ZABBIX_PASSWORD",
        "ZABBIX_TOKEN",
        "ZABBIX_MAP_NAME",
        "ZABBIX_MAP_WIDTH",
        "ZABBIX_MAP_HEIGHT",
        "ZABBIX_LAYOUT_GRID_X",
        "ZABBIX_LAYOUT_GRID_Y",
        "ZABBIX_SKIPPED_NODE_MODE",
        "ZABBIX_SKIPPED_NODE_ICON_ID",
        "ZABBIX_MAPS_CONFIG",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_load_settings_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "http://netbox.local/")
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "zbx-token")

    settings = load_settings()

    assert settings.netbox_url == "http://netbox.local"
    assert settings.netbox_topology_path == "/api/plugins/netbox_topology_views/xml-export/"
    assert settings.netbox_ignored_device_roles == ()
    assert settings.zabbix_map_width == 1920
    assert settings.zabbix_layout_grid_x == 40
    assert settings.zabbix_skipped_node_mode == "skip"
    assert settings.zabbix_skipped_node_icon_id == ""


def test_load_settings_skipped_node_image_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "http://netbox.local")
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "zbx-token")
    monkeypatch.setenv("ZABBIX_SKIPPED_NODE_MODE", "IMAGE")
    monkeypatch.setenv("ZABBIX_SKIPPED_NODE_ICON_ID", "200")

    settings = load_settings()

    assert settings.zabbix_skipped_node_mode == "image"
    assert settings.zabbix_skipped_node_icon_id == "200"


def test_load_settings_rejects_invalid_skipped_node_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "http://netbox.local")
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "zbx-token")
    monkeypatch.setenv("ZABBIX_SKIPPED_NODE_MODE", "hide")

    with pytest.raises(ConfigurationError, match="ZABBIX_SKIPPED_NODE_MODE"):
        load_settings()


def test_load_settings_reads_ignored_device_roles_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "http://netbox.local")
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("NETBOX_IGNORED_DEVICE_ROLES", "patch-panel, power-panel, patch-panel")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "zbx-token")

    settings = load_settings()

    assert settings.netbox_ignored_device_roles == ("patch-panel", "power-panel")


def test_load_settings_with_user_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "http://netbox.local")
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_USER", "Admin")
    monkeypatch.setenv("ZABBIX_PASSWORD", "secret")

    settings = load_settings()

    assert settings.zabbix_user == "Admin"
    assert settings.zabbix_password == "secret"
    assert settings.zabbix_token == ""


def test_load_settings_missing_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "zbx-token")

    with pytest.raises(ConfigurationError, match="NETBOX_URL"):
        load_settings()


def test_load_settings_requires_zabbix_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "http://netbox.local")
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")

    with pytest.raises(ConfigurationError, match="ZABBIX_TOKEN"):
        load_settings()


def _base_env(monkeypatch: pytest.MonkeyPatch, maps_path) -> None:
    monkeypatch.setenv("NETBOX_URL", "http://netbox.local")
    monkeypatch.setenv("NETBOX_TOKEN", "nb-token")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.local/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "zbx-token")
    monkeypatch.setenv("ZABBIX_MAP_NAME", "Env Map")
    monkeypatch.setenv("ZABBIX_MAPS_CONFIG", str(maps_path))


def test_load_map_definitions_falls_back_to_env_map_without_saved_maps(monkeypatch, tmp_path) -> None:
    _base_env(monkeypatch, tmp_path / "missing.json")

    definitions = load_map_definitions(load_settings())

    assert [d.name for d in definitions] == ["Env Map"]


def test_save_and_delete_map_definitions_round_trip(monkeypatch, tmp_path) -> None:
    maps_path = tmp_path / "nested" / "maps.json"
    _base_env(monkeypatch, maps_path)
    settings = load_settings()
    defaults = default_map_definition(settings)

    save_map_definition(settings, parse_map_definition({"name": "A", "topology_query": "site_id=1"}, defaults))
    save_map_definition(settings, parse_map_definition({"name": "B", "topology_query": ""}, defaults))
    # Same name again replaces in place rather than appending.
    save_map_definition(settings, parse_map_definition({"name": "A", "topology_query": "site_id=2"}, defaults))

    definitions = load_map_definitions(settings)
    assert [(d.name, d.topology_query) for d in definitions] == [("A", "site_id=2"), ("B", "")]
    assert definitions[0].width == defaults.width

    assert delete_map_definition(settings, "A") is True
    assert delete_map_definition(settings, "A") is False
    assert [d.name for d in load_map_definitions(settings)] == ["B"]


def test_parse_map_definition_validates_fields(monkeypatch, tmp_path) -> None:
    _base_env(monkeypatch, tmp_path / "maps.json")
    defaults = default_map_definition(load_settings())

    parsed = parse_map_definition(
        {"name": "A", "topology_query": "", "ignored_device_roles": "patchpanel, ,server", "width": ""},
        defaults,
    )
    assert parsed.ignored_device_roles == ("patchpanel", "server")
    assert parsed.width == defaults.width

    with pytest.raises(ConfigurationError, match="skipped_node_mode"):
        parse_map_definition({"name": "A", "topology_query": "", "skipped_node_mode": "hide"}, defaults)
    with pytest.raises(ConfigurationError, match="requires"):
        parse_map_definition({"name": " ", "topology_query": ""}, defaults)


def test_load_map_definitions_rejects_invalid_file(monkeypatch, tmp_path) -> None:
    maps_path = tmp_path / "maps.json"
    maps_path.write_text('{"not_maps": []}')
    _base_env(monkeypatch, maps_path)

    with pytest.raises(ConfigurationError, match="\"maps\" array"):
        load_map_definitions(load_settings())
