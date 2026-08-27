import pytest

from zabbix_map_sync.config import ConfigurationError, load_settings


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
